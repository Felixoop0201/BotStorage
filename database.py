import psycopg2
from psycopg2 import pool
import os
import logging
from dotenv import load_dotenv

load_dotenv()

# Берем URL базы данных из переменных окружения
DATABASE_URL = os.getenv("DATABASE_URL")

# Создаем пул соединений для стабильной работы
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
            # Таблица папок
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS folders (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    name TEXT,
                    UNIQUE(user_id, name)
                )
            ''')
            # Таблица файлов (с каскадным удалением)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS files (
                    id SERIAL PRIMARY KEY,
                    folder_id INTEGER REFERENCES folders (id) ON DELETE CASCADE,
                    file_id TEXT,
                    file_type TEXT,
                    caption TEXT
                )
            ''')
            conn.commit()
            logging.info("🛠 Таблицы в облачной БД проверены/созданы")
    finally:
        put_connection(conn)

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
            cursor.execute('SELECT name FROM folders WHERE user_id = %s', (user_id,))
            rows = cursor.fetchall()
            return [row[0] for row in rows]
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

def add_file(folder_id, file_id, file_type, caption=None):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute('INSERT INTO files (folder_id, file_id, file_type, caption) VALUES (%s, %s, %s, %s)', 
                           (folder_id, file_id, file_type, caption))
            conn.commit()
    finally:
        put_connection(conn)

def get_files(folder_id):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT file_id, file_type, caption FROM files WHERE folder_id = %s', (folder_id,))
            return cursor.fetchall()
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
