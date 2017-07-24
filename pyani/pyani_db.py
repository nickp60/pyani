# -*- coding: utf-8 -*-
"""pyani_db.py

This module provides useful functions for creating and manipulating pyani's
SQLite3 databases

(c) The James Hutton Institute 2016-2017
Author: Leighton Pritchard

Contact:
leighton.pritchard@hutton.ac.uk

Leighton Pritchard,
Information and Computing Sciences,
James Hutton Institute,
Errol Road,
Invergowrie,
Dundee,
DD6 9LH,
Scotland,
UK

The MIT License

Copyright (c) 2016-2017 The James Hutton Institute

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""

import sqlite3

# SQL SCRIPTS
#==============
# The following is SQL for various database admin tasks, defined here to
# be out of the way when reading code.

# Create database tables
#
# genomes            - a row of data, one per genome
# runs               - a row of data, one per pyani run
# comparisons        - a row of data, one per pairwise comparison
# run_genomes        - table providing many:many comparisons
#
# The intention is that a run applies to some/all genomes from the genomes
# table, and that all the relevant pairwise comparisons/results are stored
# in the comparisons table.
#
# Information about the run (when it was run, what command/method, etc.) are
# stored in the runs table.
#
# All genomes (whether used or not) are described in the genomes table. An MD5
# hash is used to uniquely identify/validate a genome that is used for any
# comparison. The path to the source data, and a description of the genome are
# stored. We expect the path information to be live, so that a comparison may
# be run or re-run. The hash will be used to verify the contents of the file
# at the end of the path, when there is a run.
#
# Each pairwise comparison is stored (forward/reverse comparisons are stored
# separately, to allow method flexibility) in the comparisons table. The
# comparisons are tied directly to genomes, but only transitively to a
# particular run; this reduces redundancy, and allows pairwise comparison
# data to be used without recalculation, if the same input genome and
# path/hash are provided.
#
# The runs_genomes table provides a link so that each genome is associated with
# all runs in which it has participated, and each run can be associated with
# all the genomes that it has participated in.

SQL_CREATEDB = """
   DROP TABLE IF EXISTS genomes;
   CREATE TABLE genomes (genome_id INTEGER PRIMARY KEY AUTOINCREMENT,
                         hash TEXT,
                         path TEXT,
                         description TEXT
                        );
   DROP TABLE IF EXISTS runs;
   CREATE TABLE runs (run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                      method TEXT,
                      cmdline TEXT,
                      date TEXT
                     );
   DROP TABLE IF EXISTS runs_genomes;
   CREATE TABLE runs_genomes(run_id INTEGER NOT NULL,
                             genome_id INTEGER NOT NULL,
                             PRIMARY KEY (run_id, genome_id),
                             FOREIGN KEY(run_id) REFERENCES
                                                   runs(run_id),
                             FOREIGN KEY(genome_id) REFERENCES 
                                                      genomes(genome_id)
                            );
   DROP TABLE IF EXISTS comparisons;
   CREATE TABLE comparisons (query_id INTEGER NOT NULL,
                             subject_id INTEGER NOT NULL,
                             identity REAL,
                             coverage REAL,
                             mismatches REAL,
                             aligned_length REAL,
                             PRIMARY KEY (query_id, subject_id)
                            );
   """


# Create an empty pyani SQLite3 database
def create_db(path):
    """Create an empty pyani SQLite3 database at the passed path."""
    conn = sqlite3.connect(path)
    with conn:
        cur = conn.cursor()
        cur.executescript(SQL_CREATEDB)

