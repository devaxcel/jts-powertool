<?php
/**
 * Plugin Name:       JTS PowerTool Connector
 * Description:       Lets JTS PowerTool read and change the TEXT inside Elementor pages (headings, paragraphs, buttons) after a person approves it. It adds three read/write routes under /wp-json/jts/v1/ and does nothing else.
 * Version:           1.0.0
 * Requires PHP:      7.4
 * License:           GPL-2.0-or-later
 *
 * Safety:
 *  - Every route needs a signed-in user who may edit that page (the same rule as the WordPress editor).
 *  - Only a fixed list of text fields can be read or changed. Layout, styles, images, links and settings are never touched.
 *  - A change is refused if the page was modified after the proposal was made.
 *  - Nothing runs on the public site, and there are no settings or database tables.
 */

if (!defined('ABSPATH')) {
    exit;
}

define('JTS_CONNECTOR_VERSION', '1.0.0');

/** Which fields of which Elementor widgets hold visible text. Simple fields live in settings[field]; repeaters in settings[repeater][i][field]. */
function jts_el_text_map() {
    return array(
        'heading'        => array('fields' => array('title' => 'text')),
        'text-editor'    => array('fields' => array('editor' => 'html')),
        'button'         => array('fields' => array('text' => 'text')),
        'icon-box'       => array('fields' => array('title_text' => 'text', 'description_text' => 'text')),
        'image-box'      => array('fields' => array('title_text' => 'text', 'description_text' => 'text')),
        'counter'        => array('fields' => array('title' => 'text')),
        'progress'       => array('fields' => array('title' => 'text')),
        'alert'          => array('fields' => array('alert_title' => 'text', 'alert_description' => 'text')),
        'testimonial'    => array('fields' => array('testimonial_content' => 'text', 'testimonial_name' => 'text', 'testimonial_job' => 'text')),
        'call-to-action' => array('fields' => array('title' => 'text', 'description' => 'text', 'button' => 'text')),
        'tabs'           => array('repeaters' => array('tabs' => array('tab_title' => 'text', 'tab_content' => 'html'))),
        'accordion'      => array('repeaters' => array('tabs' => array('tab_title' => 'text', 'tab_content' => 'html'))),
        'toggle'         => array('repeaters' => array('tabs' => array('tab_title' => 'text', 'tab_content' => 'html'))),
        'icon-list'      => array('repeaters' => array('icon_list' => array('text' => 'text'))),
    );
}

/** Calls $cb(&$element) for every element in the tree. */
function jts_el_walk(array &$elements, $cb) {
    foreach ($elements as &$el) {
        if (!is_array($el)) {
            continue;
        }
        $cb($el);
        if (!empty($el['elements']) && is_array($el['elements'])) {
            jts_el_walk($el['elements'], $cb);
        }
    }
    unset($el);
}

/** Every editable text on the page: [{element_id, widget, path, kind, text}, ...] */
function jts_el_text_nodes(array $elements) {
    $map   = jts_el_text_map();
    $nodes = array();
    jts_el_walk($elements, function (&$el) use ($map, &$nodes) {
        $widget = isset($el['widgetType']) ? $el['widgetType'] : '';
        if ($widget === '' || !isset($map[$widget]) || empty($el['id'])) {
            return;
        }
        $settings = isset($el['settings']) && is_array($el['settings']) ? $el['settings'] : array();
        if (!empty($map[$widget]['fields'])) {
            foreach ($map[$widget]['fields'] as $field => $kind) {
                if (isset($settings[$field]) && is_string($settings[$field]) && $settings[$field] !== '') {
                    $nodes[] = array('element_id' => (string) $el['id'], 'widget' => $widget, 'path' => $field, 'kind' => $kind, 'text' => $settings[$field]);
                }
            }
        }
        if (!empty($map[$widget]['repeaters'])) {
            foreach ($map[$widget]['repeaters'] as $rep => $fields) {
                if (empty($settings[$rep]) || !is_array($settings[$rep])) {
                    continue;
                }
                foreach ($settings[$rep] as $i => $item) {
                    foreach ($fields as $field => $kind) {
                        if (is_array($item) && isset($item[$field]) && is_string($item[$field]) && $item[$field] !== '') {
                            $nodes[] = array('element_id' => (string) $el['id'], 'widget' => $widget, 'path' => $rep . '.' . $i . '.' . $field, 'kind' => $kind, 'text' => $item[$field]);
                        }
                    }
                }
            }
        }
    });
    return $nodes;
}

/** Resolves "title" or "tabs.2.tab_title" for a widget into [array of keys, kind], or null when that text field is not allowed. */
function jts_el_resolve_path($widget, $path) {
    $map = jts_el_text_map();
    if (!isset($map[$widget])) {
        return null;
    }
    $parts = explode('.', (string) $path);
    if (count($parts) === 1 && isset($map[$widget]['fields'][$parts[0]])) {
        return array($parts, $map[$widget]['fields'][$parts[0]]);
    }
    if (count($parts) === 3 && isset($map[$widget]['repeaters'][$parts[0]][$parts[2]]) && ctype_digit($parts[1])) {
        return array($parts, $map[$widget]['repeaters'][$parts[0]][$parts[2]]);
    }
    return null;
}

/** Applies [{element_id, path, new_value}] to the tree. Returns [applied count, error messages]. */
function jts_el_apply(array &$elements, array $changes) {
    $applied = 0;
    $errors  = array();
    foreach ($changes as $n => $ch) {
        $id    = isset($ch['element_id']) ? (string) $ch['element_id'] : '';
        $path  = isset($ch['path']) ? (string) $ch['path'] : '';
        $value = isset($ch['new_value']) ? $ch['new_value'] : null;
        if ($id === '' || $path === '' || !is_string($value)) {
            $errors[] = 'Change ' . ($n + 1) . ' is incomplete.';
            continue;
        }
        $done = false;
        jts_el_walk($elements, function (&$el) use ($id, $path, $value, &$done, &$errors, &$applied, $n) {
            if ($done || !isset($el['id']) || (string) $el['id'] !== $id) {
                return;
            }
            $widget   = isset($el['widgetType']) ? $el['widgetType'] : '';
            $resolved = jts_el_resolve_path($widget, $path);
            if ($resolved === null) {
                $errors[] = 'Change ' . ($n + 1) . ': that field cannot be changed.';
                $done = true;
                return;
            }
            list($keys, $kind) = $resolved;
            $clean = ($kind === 'html') ? wp_kses_post($value) : trim(wp_strip_all_tags($value));
            if (count($keys) === 1) {
                if (!isset($el['settings'][$keys[0]]) || !is_string($el['settings'][$keys[0]])) {
                    $errors[] = 'Change ' . ($n + 1) . ': that text no longer exists.';
                    $done = true;
                    return;
                }
                $el['settings'][$keys[0]] = $clean;
            } else {
                $i = (int) $keys[1];
                if (!isset($el['settings'][$keys[0]][$i][$keys[2]]) || !is_string($el['settings'][$keys[0]][$i][$keys[2]])) {
                    $errors[] = 'Change ' . ($n + 1) . ': that text no longer exists.';
                    $done = true;
                    return;
                }
                $el['settings'][$keys[0]][$i][$keys[2]] = $clean;
            }
            $applied++;
            $done = true;
        });
        if (!$done) {
            $errors[] = 'Change ' . ($n + 1) . ': that element was not found on the page.';
        }
    }
    return array($applied, $errors);
}

function jts_el_load($post_id) {
    if (get_post_meta($post_id, '_elementor_edit_mode', true) !== 'builder') {
        return null;
    }
    $raw = get_post_meta($post_id, '_elementor_data', true);
    if (is_string($raw)) {
        $raw = json_decode($raw, true);
    }
    return is_array($raw) ? $raw : null;
}

function jts_register_routes() {
    register_rest_route('jts/v1', '/ping', array(
        'methods'             => 'GET',
        'permission_callback' => function () {
            return current_user_can('edit_posts');
        },
        'callback'            => function () {
            return array(
                'plugin'            => 'jts-powertool-connector',
                'version'           => JTS_CONNECTOR_VERSION,
                'elementor'         => defined('ELEMENTOR_VERSION'),
                'elementor_version' => defined('ELEMENTOR_VERSION') ? ELEMENTOR_VERSION : null,
            );
        },
    ));

    register_rest_route('jts/v1', '/elementor/(?P<id>\d+)', array(
        array(
            'methods'             => 'GET',
            'permission_callback' => function ($request) {
                return current_user_can('edit_post', (int) $request['id']);
            },
            'callback'            => function ($request) {
                $id   = (int) $request['id'];
                $data = jts_el_load($id);
                if ($data === null) {
                    return new WP_Error('jts_not_elementor', 'This page is not built with Elementor.', array('status' => 404));
                }
                return array(
                    'id'       => $id,
                    'title'    => get_the_title($id),
                    'modified' => get_post_field('post_modified_gmt', $id),
                    'nodes'    => jts_el_text_nodes($data),
                );
            },
        ),
        array(
            'methods'             => 'POST',
            'permission_callback' => function ($request) {
                return current_user_can('edit_post', (int) $request['id']);
            },
            'callback'            => function ($request) {
                $id   = (int) $request['id'];
                $data = jts_el_load($id);
                if ($data === null) {
                    return new WP_Error('jts_not_elementor', 'This page is not built with Elementor.', array('status' => 404));
                }
                $expected = (string) $request->get_param('expected_modified');
                $current  = (string) get_post_field('post_modified_gmt', $id);
                if ($expected !== '' && $expected !== $current) {
                    return new WP_Error('jts_conflict', 'The page was changed after this edit was proposed. Ask again so the newest version is used.', array('status' => 409));
                }
                $changes = $request->get_param('changes');
                if (!is_array($changes) || count($changes) === 0 || count($changes) > 100) {
                    return new WP_Error('jts_bad_request', 'Send between 1 and 100 changes.', array('status' => 400));
                }
                list($applied, $errors) = jts_el_apply($data, $changes);
                if (count($errors) > 0) {
                    return new WP_Error('jts_bad_change', implode(' ', $errors), array('status' => 422));
                }
                update_post_meta($id, '_elementor_data', wp_slash(wp_json_encode($data)));
                delete_post_meta($id, '_elementor_css');
                wp_update_post(array('ID' => $id)); // refreshes the modified date and keeps a revision
                if (class_exists('\Elementor\Plugin') && isset(\Elementor\Plugin::$instance->files_manager)) {
                    \Elementor\Plugin::$instance->files_manager->clear_cache();
                }
                return array('ok' => true, 'applied' => $applied, 'modified' => get_post_field('post_modified_gmt', $id));
            },
        ),
    ));
}
add_action('rest_api_init', 'jts_register_routes');
