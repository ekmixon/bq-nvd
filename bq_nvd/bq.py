import json

from google.api_core.exceptions import NotFound, Conflict
from google.auth.exceptions import DefaultCredentialsError
from google.cloud import bigquery


class BQ(object):
  """BigQuery object.

  Attributes:
    config: configuration data
    client: BQ client driver
  """

  def __init__(self, config):
    """Initialize BQ object with config data and BQ driver."""
    self.config = config
    try:
      self.client = bigquery.Client(project=self.config['project'])
    except DefaultCredentialsError as e:
      raise e

  def parse_bq_json_schema(self):
    """Make a BQ schema. Hat tip to nonbeing on StackOverflow for this solution.
    https://stackoverflow.com/questions/36127537/json-table-schema-to-bigquery-tableschema-for-bigquerysink

    Args:
      None

    Returns:
      schema: BQ schema object

    Raises:
      TypeError: when failing to deserialize json when the serialized json is not string, bytes, or bytearray, or when the filename is an expexcted type
      json.JSONDecodeError: when failing to deserialize json due to serialized json containing an unexpected UTF-8 BOM
    """
    def _get_field_schema(field):
      """Recursive child function for parse_bq_json_schema.

      Args:
        field: the schema field name

      Returns:
        field_schema: a schema for the field

      Raises:
        None
      """
      name = field['name']
      field_type = field.get('type', 'STRING')
      mode = field.get('mode', 'NULLABLE')
      fields = field.get('fields', [])

      if fields:
        subschema = []
        for f in fields:
          fields_res = _get_field_schema(f)
          subschema.append(fields_res)
      else:
        subschema = []

      field_schema = bigquery.SchemaField(name=name,
                                          field_type=field_type,
                                          mode=mode,
                                          fields=subschema
                                          )
      return field_schema

    schema = []
    try:
      with open(self.config['nvd_schema'], 'r') as infile:
        jsonschema = json.load(infile)
    except TypeError as e:
      raise TypeError(f'json.load failed in BQ.parse_bq_json_schema: {str(e)}')
    except json.JSONDecodeError as e:
      raise e

    for _field in jsonschema:
      schema.append(_get_field_schema(_field))

    return schema

  def make_dataset(self, dataset_name):
    """Make the NVD dataset if it's missing

    Args:
      dataset: the dataset name
      schema: relative path to schema json

    Returns:
      None

    Raises:
      None
    """
    project_name = self.config['project']
    table_name = f'{project_name}.{dataset_name}.nvd'
    try:
      d = bigquery.Dataset(f'{project_name}.{dataset_name}')
      dataset = self.client.create_dataset(d)
    except Conflict:
      # it's ok, it already exists
      pass
    try:
      t = bigquery.Table(table_name, schema=self.parse_bq_json_schema())
      table = self.client.create_table(t)
    except Conflict:
      # it's ok, it already exists
      pass

  def count_cves(self, dataset):
    """Counts the CVEs present in the specified BQ NVD dataset

    Args:
      dataset: the BigQuery dataset we're querying

    Returns:
      cve_count: int count of CVEs in the BQ dataset

    Raises:
      DefaultCredentialsError: if there's a problem with BQ auth
      TypeError: if there's an internal problem with client.query (job_config)
    """
    query = f"SELECT   COUNT(cve.CVE_data_meta.ID) AS Count FROM {dataset}.nvd"

    try:
      query_job = self.client.query(query)
      for row in query_job:
        cve_count = row['Count']
        # there's only one value in this column, so break
        continue
    except TypeError as e:
      raise e
    except NotFound as e:
      # Dataset doesn't exist yet, so create it
      self.make_dataset(dataset)
      cve_count = 0

    return cve_count

  def get_cve_ids(self, dataset):
    """Gets a list of CVE IDs from the specified dataset

    Args:
      dataset: the BigQuery dataset we're querying

    Returns:
      cve_list: list of CVE IDs

    Raises:

    """
    query = f"SELECT   cve.CVE_data_meta.ID AS ID FROM {dataset}.nvd"

    cve_list = []
    try:
      query_job = self.client.query(query)
      cve_list.extend(row['ID'] for row in query_job)
    except TypeError as e:
      raise e

    return cve_list

  def load_from_gcs(self, dataset, uri):
    """Bulk load newline delimited json from GCS into BQ

    Args:
      dataset: the BQ dataset
      uri: the GCS uri of the newline delimited json

    Returns:
      None

    Raises:
      None
    """
    project_name = self.config['project']
    dataset_name = f'{project_name}.{dataset}'
    table_name = 'nvd'

    # dataset_ref = self.client.dataset(dataset_name)
    dataset_ref = self.client.dataset(dataset)
    job_config = bigquery.LoadJobConfig()
    job_config.schema = self.parse_bq_json_schema()
    job_config.source_format = bigquery.SourceFormat.NEWLINE_DELIMITED_JSON
    job_config.ignore_unknown_values = True

    # We're going to call this synchronously so that our set calculations
    # are correct
    load_job = self.client.load_table_from_uri(
        uri,
        dataset_ref.table(table_name),
        job_config=job_config,
    )
    load_job.result()


