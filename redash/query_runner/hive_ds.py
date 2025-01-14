import logging
import sys
import base64

from redash.query_runner import *
from redash.utils import json_dumps

logger = logging.getLogger(__name__)

try:
    from pyhive import hive
    from thrift.transport import THttpClient
    enabled = True
except ImportError:
    enabled = False

COLUMN_NAME = 0
COLUMN_TYPE = 1

types_map = {
    'BIGINT_TYPE': TYPE_INTEGER,
    'TINYINT_TYPE': TYPE_INTEGER,
    'SMALLINT_TYPE': TYPE_INTEGER,
    'INT_TYPE': TYPE_INTEGER,
    'DOUBLE_TYPE': TYPE_FLOAT,
    'DECIMAL_TYPE': TYPE_FLOAT,
    'FLOAT_TYPE': TYPE_FLOAT,
    'REAL_TYPE': TYPE_FLOAT,
    'BOOLEAN_TYPE': TYPE_BOOLEAN,
    'TIMESTAMP_TYPE': TYPE_DATETIME,
    'DATE_TYPE': TYPE_DATETIME,
    'CHAR_TYPE': TYPE_STRING,
    'STRING_TYPE': TYPE_STRING,
    'VARCHAR_TYPE': TYPE_STRING
}


class Hive(BaseSQLQueryRunner):
    noop_query = "SELECT 1"

    @classmethod
    def configuration_schema(cls):
        return {
            "type": "object",
            "properties": {
                "host": {
                    "type": "string"
                },
                "port": {
                    "type": "number"
                },
                "database": {
                    "type": "string"
                },
                "username": {
                    "type": "string"
                },
            },
            "order": ["host", "port", "database", "username"],
            "required": ["host"]
        }

    @classmethod
    def annotate_query(cls):
        return False

    @classmethod
    def type(cls):
        return "hive"

    @classmethod
    def enabled(cls):
        return enabled

    def _get_tables(self, schema):
        schemas_query = "show schemas"

        tables_query = "show tables in %s"

        columns_query = "show columns in %s.%s"

        database_name_col = 'database_name'
        database_tablename_col = 'tab_name'
        column_name_col = 'field'

        schemas_query_result = self._run_query_internal(schemas_query)

        # This tests to see if we are dealing with SparkSQL as they have different column names
        # if it does we change them so that the schema refresh doesn't fail
        if len(schemas_query_result) > 0 and 'databaseName' in schemas_query_result[0]:
            database_name_col = 'databaseName'
            database_tablename_col = 'tableName'
            column_name_col = 'col_name'

        for schema_name in self._extract_column(schemas_query_result, database_name_col):
            for table_name in self._extract_column(tables_query % schema_name, database_tablename_col):
                columns_query_templated = columns_query % (schema_name, table_name)
                columns = self._extract_column(columns_query_templated, column_name_col)

                if schema_name != 'default':
                    table_name = '{}.{}'.format(schema_name, table_name)

                schema[table_name] = {'name': table_name, 'columns': columns}
        return schema.values()

    def _get_connection(self):
        host = self.configuration['host']

        connection = hive.connect(
            host=host,
            port=self.configuration.get('port', None),
            database=self.configuration.get('database', 'default'),
            username=self.configuration.get('username', None),
        )
        
        return connection
    
    def _extract_column(self, query, column_name):
        query_result = []

        if isinstance(query, str):
            query_result = self._run_query_internal(query)
        elif isinstance(query, list):
            query_result = query

        return filter(
            lambda a: len(a) > 0,
            map(
                lambda a: str(a[column_name]), 
                query_result
            )
        )

    def run_query(self, query, user):
        connection = None
        try:
            connection = self._get_connection() 
            cursor = connection.cursor()

            cursor.execute(query)

            column_names = []
            columns = []

            for column in cursor.description:
                column_name = column[COLUMN_NAME]
                column_names.append(column_name)

                columns.append({
                    'name': column_name,
                    'friendly_name': column_name,
                    'type': types_map.get(column[COLUMN_TYPE], None)
                })

            rows = [dict(zip(column_names, row)) for row in cursor]

            data = {'columns': columns, 'rows': rows}
            json_data = json_dumps(data)
            error = None
        except KeyboardInterrupt:
            if connection:
                connection.cancel()
            error = "Query cancelled by user."
            json_data = None
        finally:
            if connection:
                connection.close()

        return json_data, error


class HiveHttp(Hive):
    @classmethod
    def name(cls):
        return "Hive (HTTP)"

    @classmethod
    def type(cls):
        return 'hive_http'

    @classmethod
    def configuration_schema(cls):
        return {
            "type": "object",
            "properties": {
                "host": {
                    "type": "string"
                },
                "port": {
                    "type": "number"
                },
                "database": {
                    "type": "string"
                },
                "username": {
                    "type": "string"
                },
                "http_scheme": {
                    "type": "string",
                    "title": "HTTP Scheme (http or https)",
                    "default": "https"
                },
                "http_path": {
                    "type": "string",
                    "title": "HTTP Path"
                },
                "http_password": {
                    "type": "string",
                    "title": "Password"
                },
            },
            "order": ["host", "port", "http_path", "username", "http_password", "database", "http_scheme"],
            "secret": ["http_password"],
            "required": ["host", "http_path"]
        }

    def _get_connection(self):
        host = self.configuration['host']

        scheme = self.configuration.get('http_scheme', 'https')

        # if path is set but is missing initial slash, append it
        path = self.configuration.get('http_path', '')
        if path and path[0] != '/':
            path = '/' + path

        # if port is set prepend colon
        port = self.configuration.get('port', '')
        if port:
            port = ':' + str(port)

        http_uri = "{}://{}{}{}".format(scheme, host, port, path)

        # create transport
        transport = THttpClient.THttpClient(http_uri)

        # if username or password is set, add Authorization header
        username = self.configuration.get('username', '')
        password = self.configuration.get('http_password', '')
        if username or password:
            auth = base64.b64encode(username + ':' + password)
            transport.setCustomHeaders({'Authorization': 'Basic ' + auth})

        # create connection
        connection = hive.connect(thrift_transport=transport)
        
        return connection


register(Hive)
register(HiveHttp)
