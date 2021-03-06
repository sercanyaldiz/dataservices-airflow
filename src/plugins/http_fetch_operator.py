import shutil
from pathlib import Path
from airflow.models.baseoperator import BaseOperator
from airflow.hooks.http_hook import HttpHook
from airflow.utils.decorators import apply_defaults


class HttpFetchOperator(BaseOperator):
    """ Operator for fetching large amounts of data
        The regular SimpleHttpOperator of Airflow is not
        convenient for this, it does not store the result
    """

    template_fields = [
        "endpoint",
        "data",
        "headers",
    ]

    @apply_defaults
    def __init__(
        self,
        endpoint: str,
        data=None,
        headers=None,
        http_conn_id="http_default",
        tmp_file=None,
        *args,
        **kwargs
    ) -> None:
        self.http_conn_id = http_conn_id
        self.endpoint = endpoint
        self.headers = headers or {}
        self.http_conn_id = http_conn_id
        self.data = data or {}
        self.tmp_file = tmp_file  # or make temp file + store path in xcom
        super().__init__(*args, **kwargs)

    def execute(self, context):
        Path(self.tmp_file).parents[0].mkdir(parents=True, exist_ok=True)
        http = HttpHook(http_conn_id=self.http_conn_id, method="GET")

        self.log.info("Calling HTTP Fetch method")
        self.log.info(self.endpoint)
        response = http.run(
            self.endpoint, self.data, self.headers, extra_options={"stream": True}
        )
        # When content is encoded (gzip etc.) we need this
        # response.raw.read = functools.partial(response.raw.read, decode_content=True)
        with open(self.tmp_file, "wb") as wf:
            shutil.copyfileobj(response.raw, wf)
