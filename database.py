import psycopg2
from psycopg2 import pool
import os
import logging
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

try:
    connection_pool = psycopg2.pool.SimpleConnectionPool(1, 10, DATABASE_URL)
    logging.info("✅ Успешное подключение к облачной базе PostgreSQL")
except Exception as e:
    logging.error(f"❌ Ошибка подключения к базе: {e}")
    connection_pool = None

def get_connection():
    return connection_pool.getconn()

def put_connection(conn):
    connection_pool.putconn(conn)

def init_db():
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS folders (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    name TEXT,
                    UNIQUE(user_id, name)
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS files (
                    id SERIAL PRIMARY KEY,
                    folder_id INTEGER REFERENCES folders (id) ON DELETE CASCADE,
                    file_id TEXT,
                    file_type TEXT,
                    name TEXT,
                    caption TEXT
                )
            ''')
            conn.commit()
    finally:
        put_connection(conn)

# --- Функции Папок ---
def create_folder(user_id, name):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute('INSERT INTO folders (user_id, name) VALUES (%s, %s)', (user_id, name))
            conn.commit()
            return True
    except psycopg2.IntegrityError:
        conn.rollback()
        return False
    finally:
        put_connection(conn)

def get_folders(user_id):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT name FROM folders WHERE user_id = %s ORDER BY name', (user_id,))
            return [row[0] for row in rows] if (rows := cursor.fetchall()) else []
    finally:
        put_connection(conn)

def rename_folder(user_id, old_name, new_name):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute('UPDATE folders SET name = %s WHERE user_id = %s AND name = %s', (new_name, user_id, old_name))
            conn.commit()
            return True
    except Exception:
        conn.rollback()
        return False
    finally:
        put_connection(conn)

def delete_folder(user_id, name):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute('DELETE FROM folders WHERE user_id = %s AND name = %s', (user_id, name))
            conn.commit()
    finally:
        put_connection(conn)

def get_folder_id(user_id, name):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT id FROM folders WHERE user_id = %s AND name = %s', (user_id, name))
            row = cursor.fetchone()
            return row[0] if row else None
    finally:
        put_connection(conn)

# --- Функции Файлов ---
def add_file(folder_id, file_id, file_type, name, caption=None):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute('INSERT INTO files (folder_id, file_id, file_type, name, caption) VALUES (%s, %s, %s, %s, %s)', 
                           (folder_id, file_id, file_type, name, caption))
            conn.commit()
    finally:
        put_connection(conn)

def get_files_in_folder(folder_id):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT id, name, file_type FROM files WHERE folder_id = %s ORDER BY id DESC', (folder_id,))
            return cursor.fetchall()
    finally:
        put_connection(conn)

def get_file_details(file_id_pk):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT file_id, file_type, name, caption FROM files WHERE id = %s', (file_id_pk,))
            return cursor.fetchone()
    finally:
        put_connection(conn)

def rename_file(file_id_pk, new_name):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute('UPDATE files SET name = %s WHERE id = %s', (new_name, file_id_pk))
            conn.commit()
    finally:
        put_connection(conn)

def delete_file(file_id_pk):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute('DELETE FROM files WHERE id = %s', (file_id_pk))
            conn.commit()
    finally:
        put_connection(conn)

def search_files(user_id, query):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute('''
                SELECT f.id, f.name, fo.name 
                FROM files f 
                JOIN folders fo ON f.folder_id = fo.id 
                WHERE fo.user_id = %s AND f.name ILIKE %s
            ''', (user_id, f'%{query}%'))
            return cursor.fetchall()
    finally:
        put_connection(conn)
