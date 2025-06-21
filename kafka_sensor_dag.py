import subprocess
from airflow.decorators import dag
from airflow.providers.apache.kafka.sensors.kafka import AwaitMessageTriggerFunctionSensor
from airflow.operators.trigger_dagrun import TriggerDagRunOperator
from datetime import datetime
import json
import logging
from airflow import DAG
from airflow.providers.apache.kafka.operators.consume import ConsumeFromTopicOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.operators.python import PythonOperator
from airflow.operators.python import get_current_context



import tempfile
import os
import csv

# Get a logger
logger = logging.getLogger(__name__)

#####
'''
{
  "bootstrap.servers": "host.docker.internal:9092,host.docker.internal:9093,host.docker.internal:9094",
  "group.id": "airflow-consumer-group",
  "security.protocol": "PLAINTEXT",
  "auto.offset.reset": "earliest"
}
'''
###



# Define the name of the temporary staging table
STAGING_TABLE = 'your_table_name_staging'


def process_messages(messages):
        """
        Processes Kafka messages and writes them to a temporary CSV file.
        Returns the path to the temporary file.
        """

        context = get_current_context()
        logger.info(f"** context: ======\t {context}")
        ti = context['ti']
        logger.info(f"**task instance: ======\t {ti}") 

        # normalize single message → list
        if not isinstance(messages, (list, tuple)):
            messages = [messages]

        logger.info(f"Processing messages: ======\t {messages}")

        with tempfile.NamedTemporaryFile(mode='w+', delete=False) as tmp_file:
            writer = csv.writer(tmp_file, delimiter='\t')
            count = 0
            for message in messages:
                data = json.loads(message.value())
                logger.info(f"Count: ======\t {count}")
                logger.info(f"Message: ======\t {data}")
                payload = data.get('payload')
                field_months = payload.get('field_months')
                field_file_type = payload.get('field_file_type')
                field_related_file = payload.get('field_related_file')
                field_financial_year = payload.get('field_financial_year')
                _airbyte_raw_id = payload.get('_airbyte_raw_id')
                row = [field_months, field_file_type, field_related_file, field_financial_year, _airbyte_raw_id]
                logger.info(f"Row: ======\t {row}")
                writer.writerow(row)
                count += 1
            tmp_file_name = tmp_file.name
            ti.xcom_push(key="tmp_file_name", value=tmp_file_name)
        logger.info(f"File Name: ======\t {tmp_file_name}")
        if not tmp_file_name:
            logger.info("~~~~~~ No tmp file name - skipping ~~~~~~")
            return False
        return tmp_file_name


def upsert_to_postgres(**kwargs):
    """
    Loads data from the temporary CSV file into a staging table,
    performs an upsert into the target table, and drops the staging table.
    """
    context = get_current_context()
    ti = context['ti']
    logger.info(f"**task instance: ======\t {ti}") 
    tmp_file_name = ti.xcom_pull(task_ids='consume_kafka_messages', key='tmp_file_name')
    logger.info(f"tmp_file_name: ======\t {tmp_file_name}")

    print ("File Name: ======\t", tmp_file_name)
   
    
    # pg_hook = PostgresHook(postgres_conn_id='postgres_default')
    # conn = pg_hook.get_conn()
    # cursor = conn.cursor()

    # try:
    #     # Step 1: Create the staging table
    #     cursor.execute(f"""
    #         CREATE TEMP TABLE {STAGING_TABLE} (
    #             field_months TEXT,
    #             field_file_type TEXT,
    #             field_related_file TEXT,
    #             field_financial_year TEXT,
    #             airbyte_raw_id TEXT
    #         ) ON COMMIT DROP;
    #     """)
    #     conn.commit()

    #     # Step 2: Bulk load data into the staging table
    #     with open(file_name, 'r') as f:
    #         cursor.copy_from(f, STAGING_TABLE, sep='\t')
    #     conn.commit()

    #     # Step 3: Upsert data into the target table
    #     cursor.execute(f"""
    #         INSERT INTO portfolio_disclosure (
    #             field_months, 
    #             field_file_type, 
    #             field_related_file, 
    #             field_financial_year, 
    #             airbyte_raw_id
    #         )
    #         SELECT 
    #             field_months, 
    #             field_file_type, 
    #             field_related_file, 
    #             field_financial_year, 
    #             airbyte_raw_id 
    #         FROM {STAGING_TABLE}
    #         ON CONFLICT (airbyte_raw_id) DO UPDATE
    #         SET 
    #             field_months = EXCLUDED.field_months,
    #             field_file_type = EXCLUDED.field_file_type,
    #             field_related_file = EXCLUDED.field_related_file,
    #             field_financial_year = EXCLUDED.field_financial_year;
    #     """)
    #     conn.commit()
    #     logger.info(f"Successfully loaded data into PostgreSQL")

    # except Exception as e:
    #     logger.error(f"Error inserting data into PostgreSQL: {str(e)}")
    #     conn.rollback()
    #     raise e

    # finally:
    #     cursor.close()
    #     conn.close()
    #     # Remove the temporary file
    #     if os.path.exists(tmp_file_name):
    #         os.remove(tmp_file_name)

with DAG(
    dag_id='kafka_listener_dag',
    start_date=datetime(2025, 4, 22),
    schedule_interval='*/15 * * * *',
    catchup=False,
) as dag:
    
    def debug_xcom(**kwargs):
       print("\t\t Debug XCom val:")
       context = get_current_context()
       ti = context['ti']
       val = ti.xcom_pull(task_ids='consume_kafka_messages', key='tmp_file_name')
       print(f"Received from XCom: {val}")

    debug = PythonOperator(
        task_id="debug",
        python_callable=debug_xcom,
    )
    

    def install_requirements():
        """
        Installs core Airflow and the Kafka/Postgres providers
        into the task‐runner environment.
        """
        packages = [
            "apache-airflow-providers-postgres",
        ]
        # run pip install in the same interpreter
        subprocess.check_call(["pip", "install", *packages])

    install_deps = PythonOperator(task_id="install_requirements",python_callable=install_requirements)


    consume_messages = ConsumeFromTopicOperator(
        task_id='consume_kafka_messages',
        topics=['mutualfundserver.public.portfolio_disclousre'],
        kafka_config_id='kafka_default',
        apply_function="kafka_dags.kafka_listener_dag.process_messages",
        do_xcom_push=True,
        max_messages=100,
        poll_timeout=60,
        commit_cadence="end_of_batch",
    )

    upsert_data = PythonOperator(
        task_id='upsert_postgres',
        python_callable=upsert_to_postgres
    )

    install_deps >> consume_messages >> debug >> upsert_data


# # move apply_function to module level so it can be referenced by string path
# def kafka_apply_function(message=None, context=None, jinja_env=None):
#     logger.info("======= KAFKA MESSAGE RECEIVED =======")
#     logger.info(f"Message: {message}")
#     logger.info(f"Context: {context}")
#     logger.info(f"Jinja Env: {jinja_env}")
    
#     if message is None:
#         logger.info("Message is None - skipping")
#         return False
    
#     try:
#         # Parse the message value as JSON and build payload
#         data = json.loads(message.value())
#         logger.info(f"Parsed message data: {data}")
#         # Build a JSON-serializable payload to pass to event_triggered_function
#         return {
#             "message_data": data,
#             "topic": message.topic(),
#             "offset": message.offset(),
#             "timestamp": message.timestamp()[1] if isinstance(message.timestamp(), tuple) else None,
#         }
#     except Exception as e:
#         logger.error(f"Error processing message: {str(e)}")
#         return False

# @dag(start_date=datetime(2025, 4, 20), schedule_interval=None, catchup=False)
# def kafka_listener_dag():
#     def event_triggered_function(event, **kwargs):
#         """First param is the payload returned by kafka_apply_function"""
        
#         logger.info(f"===== TRIGGERED EVENT with payload: {event} =====")
#         # Trigger the target DAG, passing the payload directly as conf
#         TriggerDagRunOperator(
#             task_id="trigger_target_dag",
#             trigger_dag_id="trigger_fastapi_job",
#             conf={""},
#         ).execute(context=kwargs)

#     # The Kafka sensor that listens for messages
#     AwaitMessageTriggerFunctionSensor(
#         task_id="listen_to_kafka",
#         kafka_config_id="kafka_default",
#         topics=["aiven_pg.public.portfolio_disclousre"],
#         apply_function="kafka_dags.kafka_listener_dag.kafka_apply_function",
#         event_triggered_function=event_triggered_function,
#     )

# kafka_listener_dag = kafka_listener_dag()
