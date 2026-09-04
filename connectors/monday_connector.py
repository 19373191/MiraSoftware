"""
M.I.R.A. Monday.com Integration Connector.

Handles API communication with Monday.com GraphQL API for querying items, updating column statuses,
and posting update comments.
"""

from typing import Any, Dict, List, Optional
import requests
from config import settings
from utils.logger import logger


class MondayConnector:
    """Connector class for managing interactions with Monday.com GraphQL API."""

    def __init__(self, api_key: Optional[str] = None, board_id: Optional[str] = None):
        self.api_key = api_key or settings.monday_api_key
        self.board_id = board_id or settings.monday_board_id
        self.api_url = "https://api.monday.com/v2"

    def _get_headers(self) -> Dict[str, str]:
        """Constructs headers required for Monday.com GraphQL API request."""
        if not self.api_key:
            logger.warning("Monday.com API Key is not set in configuration.")
        return {
            "Authorization": self.api_key or "",
            "Content-Type": "application/json",
            "API-Version": "2023-10",
        }

    def execute_query(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Executes a GraphQL query or mutation against Monday.com API (POST https://api.monday.com/v2).

        Args:
            query: GraphQL query or mutation string.
            variables: Optional GraphQL variables dictionary.

        Returns:
            Dict[str, Any]: Parsed JSON response.
        """
        headers = self._get_headers()
        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        logger.info("Executing Monday.com GraphQL request...")
        response = requests.post(self.api_url, json=payload, headers=headers, timeout=15)
        response.raise_for_status()

        result = response.json()
        if "errors" in result:
            logger.error("Monday.com API returned GraphQL errors: %s", result["errors"])
            raise ValueError(f"Monday.com API returned GraphQL errors: {result['errors']}")
        return result

    def query_board_items(self, board_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Queries item records from a specified Monday.com board using Monday's GraphQL API.

        Args:
            board_id: Target board ID. Defaults to configured board_id.
            limit: Maximum items to retrieve.

        Returns:
            List[Dict[str, Any]]: Raw board item objects including subitems.
        """
        target_board_id = board_id or self.board_id
        query = """
        query ($board_id: [ID!], $limit: Int!) {
            boards (ids: $board_id) {
                id
                name
                items_page (limit: $limit) {
                    items {
                        id
                        name
                        column_values {
                            id
                            column {
                                title
                            }
                            text
                            value
                            ... on BoardRelationValue {
                                linked_items {
                                    id
                                    name
                                    column_values {
                                        id
                                        column {
                                            title
                                        }
                                        text
                                        value
                                    }
                                }
                            }
                        }
                        subitems {
                            id
                            name
                            column_values {
                                id
                                column {
                                    title
                                }
                                text
                                value
                                ... on BoardRelationValue {
                                    linked_items {
                                        id
                                        name
                                        column_values {
                                            id
                                            column {
                                                title
                                            }
                                            text
                                            value
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        """
        variables = {"board_id": [target_board_id], "limit": limit}
        logger.info("Querying Monday board items for board_id: %s (limit: %d)", target_board_id, limit)
        data = self.execute_query(query, variables)

        try:
            boards = data.get("data", {}).get("boards", [])
            if boards:
                items = boards[0].get("items_page", {}).get("items", [])
                
                # Post-process columns to copy column.title to title
                def clean_cols(cols):
                    if isinstance(cols, list):
                        for col in cols:
                            if isinstance(col, dict):
                                if "column" in col and isinstance(col["column"], dict):
                                    col["title"] = col["column"].get("title")
                                # Recursively check linked items
                                if "linked_items" in col and isinstance(col["linked_items"], list):
                                    for li in col["linked_items"]:
                                        if isinstance(li, dict) and "column_values" in li:
                                            clean_cols(li["column_values"])
                                            
                for item in items:
                    clean_cols(item.get("column_values"))
                    for sub in item.get("subitems", []):
                        clean_cols(sub.get("column_values"))
                
                logger.info("Successfully fetched %d items from Monday board.", len(items))
                return items
        except (KeyError, IndexError) as e:
            logger.error("Failed to parse Monday board items from GraphQL response: %s", e)

        return []

    def get_board_items(self, board_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Alias for query_board_items."""
        return self.query_board_items(board_id=board_id, limit=limit)

    def update_item_status(
        self,
        item_id: str,
        status_label: str,
        column_id: str = "status",
        board_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Changes a status column value on a Monday item (e.g. 'Invoiced' or 'Failed: Validation Error').

        Args:
            item_id: Monday item ID.
            status_label: Target status label text (e.g. 'Invoiced', 'Failed').
            column_id: Target status column key (default: 'status').
            board_id: Optional board ID.

        Returns:
            Dict[str, Any]: Response from Monday.com API.
        """
        target_board_id = board_id or self.board_id
        mutation = """
        mutation ($board_id: ID!, $item_id: ID!, $column_id: String!, $value: String!) {
            change_simple_column_value (
                board_id: $board_id,
                item_id: $item_id,
                column_id: $column_id,
                value: $value
            ) {
                id
                name
            }
        }
        """
        variables = {
            "board_id": str(target_board_id),
            "item_id": str(item_id),
            "column_id": str(column_id),
            "value": str(status_label),
        }
        logger.info(
            "Updating Monday item %s status column '%s' to label '%s'",
            item_id,
            column_id,
            status_label,
        )
        return self.execute_query(mutation, variables)

    def post_item_update(self, item_id: str, body_text: str) -> Dict[str, Any]:
        """
        Posts an update comment directly on a Monday item's update feed.

        Args:
            item_id: Target Monday item ID.
            body_text: Update message text or error trace details.

        Returns:
            Dict[str, Any]: GraphQL response from Monday.com API.
        """
        mutation = """
        mutation ($item_id: ID!, $body: String!) {
            create_update (item_id: $item_id, body: $body) {
                id
            }
        }
        """
        variables = {
            "item_id": str(item_id),
            "body": str(body_text),
        }
        logger.info("Posting update comment on Monday item %s", item_id)
        return self.execute_query(mutation, variables)

    def query_boards(self) -> List[Dict[str, Any]]:
        """
        Queries all boards visible to the Personal API Token.

        Returns:
            List[Dict[str, Any]]: List of board dicts with 'id' and 'name'.
        """
        query = """
        query {
            boards (limit: 100) {
                id
                name
            }
        }
        """
        logger.info("Querying Monday.com boards list...")
        try:
            data = self.execute_query(query)
            return data.get("data", {}).get("boards", [])
        except Exception as e:
            logger.error("Failed to query boards list: %s", e)
            return []

    def query_board_columns(self, board_id: str) -> List[Dict[str, Any]]:
        """
        Queries all column definitions for a specified Monday.com board.
        """
        query = """
        query ($board_id: [ID!]) {
            boards (ids: $board_id) {
                columns {
                    id
                    title
                    type
                    settings_str
                }
            }
        }
        """
        variables = {"board_id": [board_id]}
        logger.info("Querying columns for Monday board: %s", board_id)
        try:
            data = self.execute_query(query, variables)
            boards = data.get("data", {}).get("boards", [])
            if boards:
                return boards[0].get("columns", [])
        except Exception as e:
            logger.error("Failed to query columns for board %s: %s", board_id, e)
        return []


    def create_item(
        self,
        board_id: str,
        item_name: str,
        column_values: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Creates a new item on a specified Monday.com board.

        Args:
            board_id: Target board ID.
            item_name: The name of the new item.
            column_values: Optional dictionary of column values (e.g. {"status": "Active"}).

        Returns:
            Dict[str, Any]: Parsed JSON response.
        """
        import json
        mutation = """
        mutation ($board_id: ID!, $item_name: String!, $column_values: JSON) {
            create_item (
                board_id: $board_id,
                item_name: $item_name,
                column_values: $column_values
            ) {
                id
                name
            }
        }
        """
        variables = {
            "board_id": str(board_id),
            "item_name": str(item_name),
        }
        if column_values:
            variables["column_values"] = json.dumps(column_values)

        logger.info("Creating new Monday item '%s' on board %s", item_name, board_id)
        return self.execute_query(mutation, variables)

    def create_subitem(
        self,
        parent_item_id: str,
        item_name: str,
        column_values: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Creates a new subitem on Monday.com under the specified parent item ID.
        """
        import json
        mutation = """
        mutation ($parent_item_id: ID!, $item_name: String!, $column_values: JSON) {
            create_subitem (
                parent_item_id: $parent_item_id,
                item_name: $item_name,
                column_values: $column_values
            ) {
                id
                name
            }
        }
        """
        variables = {
            "parent_item_id": str(parent_item_id),
            "item_name": str(item_name),
        }
        if column_values:
            variables["column_values"] = json.dumps(column_values)

        logger.info("Creating new Monday subitem '%s' under parent item %s", item_name, parent_item_id)
        return self.execute_query(mutation, variables)
