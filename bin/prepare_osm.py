#!/usr/bin/env python

import sys
import io
import os
import math
import random
import argparse
import subprocess
import tempfile

from time import time, sleep
from urllib.parse import urlparse
from pkg_resources import resource_exists, resource_listdir, resource_string

import psycopg2
from psycopg2.extras import DictCursor

#
# DB-Utility functions
#

def open_db(url, cursor_name=None):
    conn = psycopg2.connect(url, cursor_factory=DictCursor)
    if cursor_name is None:
        cursor = conn.cursor()
    else:
        cursor = conn.cursor(name=cursor_name)
    return cursor

def load_sql(db, path):
    try:
        # assume we are in a virtualenv first
        if resource_exists('osmgeocoder', path):
            sql_files = list(resource_listdir('osmgeocoder', path))
            sql_files.sort()
            for f in sql_files:
                print(f'Executing {f}...')
                db.execute(resource_string('osmgeocoder', os.path.join(path, f)))
    except ModuleNotFoundError:
        # if not found, assume we have been started from a source checkout
        my_dir = os.path.dirname(os.path.abspath(__file__))
        sql_path = os.path.abspath(os.path.join(my_dir, '../osmgeocoder/', path))
        sql_files = [os.path.join(sql_path, f) for f in os.listdir(sql_path) if os.path.isfile(os.path.join(sql_path, f))]
        sql_files.sort()

        for f in sql_files:
            print(f'Executing {f}...')
            with open(f, 'r') as fp:
                db.execute(fp.read())


def prepare_db(db):
    load_sql(db, 'data/sql/prepare')

def optimize_db(db):
    load_sql(db, 'data/sql/optimize')

def close_db(db):
    conn = db.connection
    conn.commit()

    if db.name is None:
        db.close()
    conn.close()


def imposm_import(db_url, data_file, tmp_dir, optimize):
    mapping_file = None
    temp = None
    try:
        # assume we are in a virtualenv first
        if resource_exists('osmgeocoder', 'data/imposm_mapping.yml'):
            data = resource_string('osmgeocoder', 'data/imposm_mapping.yml')
            temp = tempfile.NamedTemporaryFile()
            temp.write(data)
            temp.seek(0)
            mapping_file = temp.name
    except ModuleNotFoundError:
        # if not found, assume we have been started from a source checkout
        my_dir = os.path.dirname(os.path.abspath(__file__))
        mapping_file = os.path.abspath(os.path.join(my_dir, '../osmgeocoder/data/imposm_mapping.yml'))

    args = [
        'imposm',
        'import',
        '-connection',
        db_url.replace('postgres', 'postgis'),
        '-mapping',
        mapping_file,
        '-read',
        data_file,
        '-cachedir',
        os.path.join(tmp_dir, 'imposm3'),
        '-overwritecache',
        '-write',
    ]
    if optimize:
        args.append('-optimize')
    args.append('-deployproduction')
    print(args)
    subprocess.run(args)

    if temp is not None:
        temp.close()

def dump(db_url, filename, threads):
    print(f'Dumping database into directory {filename}...')
    parsed = urlparse(db_url)
    args = [
        'pg_dump',
        '-v',                    # verbose
        '-F', 'd',               # directory type
        '-j', str(threads),      # number of concurrent jobs
        '-Z', '9',               # maximum compression
        '-O',                    # no owners
        '-x',                    # no privileges
        '-f', filename,          # destination dir
        '-h', parsed.hostname,
    ]

    if parsed.port is not None:
        args.append('-p')
        args.append(str(parsed.port))
    if parsed.username is not None:
        args.append('-U')
        args.append(parsed.username)
    args.append(parsed.path[1:])
    print(" ".join(args))
    #subprocess.run(args)

#
# Cmdline interface
#

def parse_cmdline():
    parser = argparse.ArgumentParser(description='OpenStreetMap Geocoder preparation script')
    parser.add_argument(
        '--db',
        type=str,
        dest='db_url',
        required=True,
        help='Postgis DB URL'
    )
    parser.add_argument(
        '--import-data',
        type=str,
        dest='data_file',
        help='OpenStreetMap data file to import'
    )
    parser.add_argument(
        '--optimize',
        dest='optimize',
        action='store_true',
        default=False,
        help='Optimize DB Tables and create indices'
    )
    parser.add_argument(
        '--dump',
        type=str,
        dest='dump_file',
        help='Dump the converted data into a pg_dump file to be imported on another server'
    )
    parser.add_argument(
        '--tmpdir',
        type=str,
        dest='tmp',
        default='/tmp',
        help='Temp dir for imports (needs at least 1.5x the amount of space of the import file)'
    )

    return parser.parse_args()


if __name__ == '__main__':
    args = parse_cmdline()
    db = open_db(args.db_url)
    prepare_db(db)
    if args.data_file is not None:
        imposm_import(args.db_url, args.data_file, args.tmp, args.optimize)
    if args.optimize:
        optimize_db(db)
    close_db(db)
    if args.dump_file:
        dump(args.db_url, args.dump_file, 4)
