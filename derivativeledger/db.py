"""Per-request database connections; SQL remains the source of truth."""
from flask import current_app, g
import mysql.connector
from pymongo import MongoClient


def mysql_connection():
    if "mysql" not in g:
        g.mysql = mysql.connector.connect(
            host=current_app.config["MYSQL_HOST"], port=current_app.config["MYSQL_PORT"],
            database=current_app.config["MYSQL_DATABASE"], user=current_app.config["MYSQL_USER"],
            password=current_app.config["MYSQL_PASSWORD"], autocommit=False,
        )
    return g.mysql


def mongo_database():
    if "mongo_client" not in g:
        g.mongo_client = MongoClient(current_app.config["MONGO_URI"], serverSelectionTimeoutMS=2500)
    return g.mongo_client[current_app.config["MONGO_DATABASE"]]


def close_connections(_error=None):
    connection = g.pop("mysql", None)
    if connection is not None and connection.is_connected():
        connection.close()
    client = g.pop("mongo_client", None)
    if client is not None:
        client.close()


def query(sql, params=(), *, one=False, commit=False):
    connection = mysql_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(sql, params)
        rows = cursor.fetchone() if one else cursor.fetchall()
        if commit:
            connection.commit()
        return rows
    except Exception:
        if commit:
            connection.rollback()
        raise
    finally:
        cursor.close()


def execute(sql, params=()):
    connection = mysql_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(sql, params)
        connection.commit()
        return cursor.lastrowid, cursor.rowcount
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
