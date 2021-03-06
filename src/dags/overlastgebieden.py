import pathlib
from airflow import DAG
from airflow.operators.bash_operator import BashOperator
from airflow.operators.postgres_operator import PostgresOperator
from postgres_check_operator import PostgresCheckOperator, PostgresValueCheckOperator
from swift_operator import SwiftOperator
from postgres_files_operator import PostgresFilesOperator

from common import (
    vsd_default_args,
    slack_webhook_token,
    MessageOperator,
    DATAPUNT_ENVIRONMENT,
)

from common.sql import (
    SQL_TABLE_RENAME,
    SQL_CHECK_COUNT,
    SQL_CHECK_GEO,
    SQL_CHECK_COLNAMES,
)

PROCESS_TABLE = """
    DELETE FROM overlastgebieden_new WHERE wkb_geometry is null;
    -- insert polygons valid, as duplicate, unpacking them where needed
    INSERT INTO overlastgebieden_new (wkb_geometry, oov_naam, type, url)
    SELECT b.geom wkb_geometry, oov_naam, type, url FROM
    (
        SELECT oov_naam, type, url, (ST_Dump(ST_CollectionExtract(ST_MakeValid(ST_Multi(wkb_geometry)), 3))).geom as geom FROM
        (
            SELECT * FROM overlastgebieden_new WHERE ST_IsValid(wkb_geometry) = false
        ) a
    ) b;
    -- remove invalid polygons (duplicates were inserted in previous statement)
    DELETE FROM overlastgebieden_new WHERE ST_IsValid(wkb_geometry) = false;
"""

dag_id = "overlastgebieden"
data_path = pathlib.Path(__file__).resolve().parents[1] / "data" / dag_id


def checker(records, pass_value):
    found_colnames = set(r[0] for r in records)
    return found_colnames >= set(pass_value)


with DAG(dag_id, default_args=vsd_default_args, template_searchpath=["/"],) as dag:

    tmp_dir = f"/tmp/{dag_id}"
    colnames = [
        "ogc_fid",
        "wkb_geometry",
        "oov_naam",
        "type",
        "url",
    ]
    fetch_shp_files = []

    slack_at_start = MessageOperator(
        task_id="slack_at_start",
        http_conn_id="slack",
        webhook_token=slack_webhook_token,
        message=f"Starting {dag_id} ({DATAPUNT_ENVIRONMENT})",
        username="admin",
    )

    for ext in ("dbf", "prj", "shp", "shx"):
        file_name = f"OOV_gebieden_totaal.{ext}"
        fetch_shp_files.append(
            SwiftOperator(
                task_id=f"fetch_shp_{ext}",
                container=dag_id,
                object_id=file_name,
                output_path=f"/tmp/{dag_id}/{file_name}",
            )
        )

    extract_shp = BashOperator(
        task_id="extract_shp",
        bash_command=f"ogr2ogr -f 'PGDump' -t_srs EPSG:28992 -skipfailures -nln {dag_id}_new "
        f"{tmp_dir}/{dag_id}.sql {tmp_dir}/OOV_gebieden_totaal.shp",
    )

    convert_shp = BashOperator(
        task_id="convert_shp",
        bash_command=f"iconv -f iso-8859-1 -t utf-8  {tmp_dir}/{dag_id}.sql > "
        f"{tmp_dir}/{dag_id}.utf8.sql",
    )

    create_tables = PostgresFilesOperator(
        task_id="create_tables", sql_files=[f"{tmp_dir}/{dag_id}.utf8.sql"],
    )

    process_table = PostgresOperator(task_id="process_table", sql=PROCESS_TABLE,)

    check_count = PostgresCheckOperator(
        task_id="check_count",
        sql=SQL_CHECK_COUNT,
        params=dict(tablename=f"{dag_id}_new", mincount=110),
    )

    check_colnames = PostgresValueCheckOperator(
        task_id="check_colnames",
        sql=SQL_CHECK_COLNAMES,
        pass_value=colnames,
        result_checker=checker,
        params=dict(tablename=f"{dag_id}_new"),
    )

    check_geo = PostgresCheckOperator(
        task_id="check_geo",
        sql=SQL_CHECK_GEO,
        params=dict(tablename=f"{dag_id}_new", geotype="ST_Polygon",),
    )

    rename_table = PostgresOperator(
        task_id="rename_table", sql=SQL_TABLE_RENAME, params=dict(tablename=dag_id),
    )

(
    slack_at_start
    >> fetch_shp_files
    >> extract_shp
    >> convert_shp
    >> create_tables
    >> process_table
    >> [check_count, check_colnames, check_geo]
    >> rename_table
)
