import unittest
from unittest.mock import patch, MagicMock
from app.services.usage_service import (
    calculate_token_cost,
    record_api_usage,
    get_usage_summary,
    get_usage_logs,
    recalculate_all_usage_costs,
)

class TestUsageService(unittest.TestCase):
    def test_calculate_token_cost(self):
        cost = calculate_token_cost('claude-3-5-sonnet', 1_000_000, 1_000_000)
        self.assertEqual(cost, 18.0)

        cost_haiku = calculate_token_cost('claude-3-5-haiku', 1_000_000, 1_000_000)
        self.assertEqual(cost_haiku, 6.00)

        cost_haiku_45 = calculate_token_cost('claude-haiku-4-5-20251001', 1_000_000, 1_000_000)
        self.assertEqual(cost_haiku_45, 6.00)

    @patch('app.services.usage_service.get_db_connection')
    def test_record_api_usage(self, mock_get_db):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_get_db.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = {'id': 1, 'created_at': None}

        result = record_api_usage(
            channel_id='C12345',
            model='claude-3-5-sonnet',
            input_tokens=1000,
            output_tokens=500,
        )

        self.assertEqual(result['total_tokens'], 1500)
        self.assertEqual(result['channel_id'], 'C12345')
        self.assertIn('cost_usd', result)

    @patch('app.services.usage_service.get_db_connection')
    def test_get_usage_summary(self, mock_get_db):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_get_db.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        mock_cur.fetchone.return_value = {
            'total_calls': 5,
            'total_input_tokens': 5000,
            'total_output_tokens': 2000,
            'total_tokens': 7000,
            'total_cost_usd': 0.045,
        }
        mock_cur.fetchall.side_effect = [
            [
                {
                    'channel_id': 'C12345',
                    'channel_name': 'general',
                    'calls': 5,
                    'input_tokens': 5000,
                    'output_tokens': 2000,
                    'total_tokens': 7000,
                    'total_cost_usd': 0.045,
                }
            ],
            [
                {
                    'user_id': 'U12345',
                    'calls': 5,
                    'input_tokens': 5000,
                    'output_tokens': 2000,
                    'total_tokens': 7000,
                    'total_cost_usd': 0.045,
                }
            ],
            [
                {
                    'channel_id': 'C12345',
                    'channel_name': 'general',
                    'user_id': 'U12345',
                    'calls': 5,
                    'input_tokens': 5000,
                    'output_tokens': 2000,
                    'total_tokens': 7000,
                    'total_cost_usd': 0.045,
                }
            ],
            [
                {
                    'workspace_id': 'T01...',
                    'workspace_name': 'Axcel World',
                }
            ],
        ]

        summary = get_usage_summary()
        self.assertEqual(summary['total_calls'], 5)
        self.assertEqual(summary['total_tokens'], 7000)
        self.assertEqual(len(summary['by_channel']), 1)
        self.assertEqual(len(summary['by_user']), 1)
        self.assertEqual(len(summary['by_channel_user']), 1)

    @patch('app.services.usage_service.get_db_connection')
    def test_recalculate_all_usage_costs(self, mock_get_db):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_get_db.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchall.return_value = [
            {
                'id': 10,
                'model': 'claude-haiku-4-5-20251001',
                'input_tokens': 5000,
                'output_tokens': 100,
                'cost_usd': 0.0165,
            }
        ]

        res = recalculate_all_usage_costs()
        self.assertEqual(res['status'], 'success')
        self.assertEqual(res['updated_count'], 1)
        mock_cur.execute.assert_called()
        mock_conn.commit.assert_called_once()

if __name__ == '__main__':
    unittest.main()
