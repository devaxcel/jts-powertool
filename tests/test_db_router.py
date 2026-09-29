import unittest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from app.main import app
from app.db_router import _clear_table_records


class TestDbRouterClear(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)

    def test_clear_table_records_truncate_success(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        _clear_table_records(mock_conn, "test_table")

        self.assertEqual(mock_conn.commit.call_count, 1)
        mock_cursor.execute.assert_called_once()
        query_arg = mock_cursor.execute.call_args[0][0]
        self.assertIn("TRUNCATE TABLE", str(query_arg))

    def test_clear_table_records_fallback_to_delete(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        # Simulate TRUNCATE RESTART IDENTITY failing and TRUNCATE CASCADE failing
        mock_cursor.execute.side_effect = [
            Exception("must be owner of sequence test_table_id_seq"),
            Exception("must be owner of table test_table"),
            None,  # DELETE FROM succeeds
            None,  # SELECT pg_get_serial_sequence succeeds
            None,  # setval succeeds
        ]
        mock_cursor.fetchone.return_value = ("test_table_id_seq",)

        _clear_table_records(mock_conn, "test_table")

        # It should rollback on failures and eventually commit
        self.assertTrue(mock_conn.rollback.called)
        self.assertTrue(mock_conn.commit.called)

    def test_clear_specific_table_endpoint_not_found(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_cursor.fetchone.return_value = None  # Table does not exist

        with patch("app.db_router.get_db_connection", return_value=mock_conn):
            response = self.client.post("/database/api/table/non_existent/clear")
            self.assertEqual(response.status_code, 404)

    def test_clear_specific_table_endpoint_success(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ("telemetry_logs",)

        with patch("app.db_router.get_db_connection", return_value=mock_conn), \
             patch("app.db_router._clear_table_records") as mock_clear:
            response = self.client.post("/database/api/table/telemetry_logs/clear")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["status"], "ok")
            mock_clear.assert_called_once_with(mock_conn, "telemetry_logs")

    def test_clear_all_tables_endpoint(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [("table1",), ("table2",)]

        with patch("app.db_router.get_db_connection", return_value=mock_conn), \
             patch("app.db_router._clear_table_records") as mock_clear:
            response = self.client.post("/database/api/clear-all")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["status"], "ok")
            self.assertEqual(mock_clear.call_count, 2)

    def test_serialize_value_decimal_and_types(self):
        from decimal import Decimal
        from datetime import datetime
        from uuid import uuid4
        from app.db_router import serialize_value

        self.assertEqual(serialize_value(Decimal("0.010500")), 0.0105)
        now = datetime.now()
        self.assertEqual(serialize_value(now), now.isoformat())
        u = uuid4()
        self.assertEqual(serialize_value(u), str(u))
        self.assertEqual(serialize_value({"cost": Decimal("1.25"), "count": 5}), {"cost": 1.25, "count": 5})
        self.assertEqual(serialize_value([Decimal("2.50")]), [2.5])


if __name__ == "__main__":
    unittest.main()
