import psycopg2
from psycopg2 import pool
import os
import logging
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

# Используем ThreadedConnectionPool для работы в многопоточном режиме
try:
    connection_pool = psycopg2.pool.ThreadedConnectionPool(1, 20, DATABASE_URL)
    logging.info("✅ Успешное подключение к пулу потоков PostgreSQL")
except Exception as e:
    logging.error(f"❌ Ошибка подключения к базе: {e}")
    connection_pool = None

def get_connection():
    try:
        if connection_pool:
            return connection_pool.getconn()
    except Exception as e:
        logging.error(f"Ошибка получения соединения: {e}")
    return None

def put_connection(conn):
    if connection_pool and conn:
        try:
            connection_pool.putconn(conn)
        except Exception as e:
            logging.error(f"Ошибка возврата соединения: {e}")

def init_db():
    conn = get_connection()
    if not conn: return
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
    if not conn: return False
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
    if not conn: return []
    try:
        with conn.cursor() as cursor:
            cursor.execute('''
                SELECT f.name, COUNT(fi.id) 
                FROM folders f 
                LEFT JOIN files fi ON f.id = fi.folder_id 
                WHERE f.user_id = %s 
                GROUP BY f.id, f.name 
                ORDER BY f.name
            ''', (user_id,))
            rows = cursor.fetchall()
            return rows if rows else []
    finally:
        put_connection(conn)

def rename_folder(user_id, old_name, new_name):
    conn = get_connection()
    if not conn: return False
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
    if not conn: return
    try:
        with conn.cursor() as cursor:
            cursor.execute('DELETE FROM folders WHERE user_id = %s AND name = %s', (user_id, name))
            conn.commit()
    finally:
        put_connection(conn)

def get_folder_id(user_id, name):
    conn = get_connection()
    if not conn: return None
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
    if not conn: return
    try:
        with conn.cursor() as cursor:
            cursor.execute('INSERT INTO files (folder_id, file_id, file_type, name, caption) VALUES (%s, %s, %s, %s, %s)', 
                           (folder_id, file_id, file_type, name, caption))
            conn.commit()
    finally:
        put_connection(conn)

def get_files_in_folder(folder_id):
    conn = get_connection()
    if not conn: return []
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT id, name, file_type FROM files WHERE folder_id = %s ORDER BY id DESC', (folder_id,))
            return cursor.fetchall()
    finally:
        put_connection(conn)

def get_file_details(file_id_pk):
    conn = get_connection()
    if not conn: return None
    try:
        with conn.cursor() as cursor:
            cursor.execute('''
                SELECT f.file_id, f.file_type, f.name, f.caption, fo.name 
                FROM files f 
                JOIN folders fo ON f.folder_id = fo.id 
                WHERE f.id = %s
            ''', (file_id_pk,))
            return cursor.fetchone()
    finally:
        put_connection(conn)

def rename_file(file_id_pk, new_name):
    conn = get_connection()
    if not conn: return
    try:
        with conn.cursor() as cursor:
            cursor.execute('UPDATE files SET name = %s WHERE id = %s', (new_name, file_id_pk))
            conn.commit()
    finally:
        put_connection(conn)

def delete_file(file_id_pk):
    conn = get_connection()
    if not conn: return
    try:
        with conn.cursor() as cursor:
            cursor.execute('DELETE FROM files WHERE id = %s', (file_id_pk,))
            conn.commit()
    finally:
        put_connection(conn)

def search_files(user_id, query):
    conn = get_connection()
    if not conn: return []
    try:
        with conn.cursor() as cursor:
            cursor.execute('''
                SELECT f.id, f.name, fo.name, f.file_type 
                FROM files f 
                JOIN folders fo ON f.folder_id = fo.id 
                WHERE fo.user_id = %s AND f.name ILIKE %s
            ''', (user_id, f'%{query}%'))
            return cursor.fetchall()
    finally:
        put_connection(conn)
