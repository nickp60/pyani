# -*- coding: utf-8 -*-
"""Module providing subcommand functions for pyani.py script.

- download:      download assemblies from NCBI
- classify:      classify ANI results

The code in this module should mediate between the user via CLI and the actual
'lifting' code in the pyani module - it should not be implementing
calculations.

This module expects the use of a logger in function calls, as all functions
should only be called in the context of a CLI interaction with the user, and
this enforces logging.

(c) The James Hutton Institute 2017
Author: Leighton Pritchard
Contact: leighton.pritchard@hutton.ac.uk
Leighton Pritchard,
Information and Computing Sciences,
James Hutton Institute,
Errol Road,
Invergowrie,
Dundee,
DD2 5DA,
Scotland,
UK

The MIT License

Copyright (c) 2017 The James Hutton Institute
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

import datetime
import os
import sqlite3

from collections import namedtuple
from itertools import combinations

from Bio import SeqIO
from tqdm import tqdm

from .. import (download, anim, run_sge, pyani_config, pyani_classify,
                pyani_graphics, pyani_tools, pyani_db, pyani_files,
                pyani_jobs, pyani_report)
from ..pyani_tools import last_exception
from .. import run_multiprocessing as run_mp

from . import tools


# Named tuple describing a pairwise comparison
Comparison = namedtuple("Comparison",
                        "query_id subject_id cmdline outfile")


# Download sequence/class/label data from NCBI
def subcmd_download(args, logger):
    """Download assembled genomes in subtree of passed NCBI taxon ID."""
    # Create output directory, respecting force/noclobber
    if not args.dryrun:
        tools.make_outdir(args.outdir, args.force, args.noclobber, logger)
    else:
        logger.warning("Dry run only: will not overwrite or download")

    # Set Entrez email
    download.set_ncbi_email(args.email)
    logger.info("Setting Entrez email address: %s", args.email)

    # Get list of taxon IDs to download
    taxon_ids = download.split_taxa(args.taxon)
    logger.info("Taxon IDs received: %s", taxon_ids)

    # Get assembly UIDs for each taxon
    asm_dict = tools.make_asm_dict(taxon_ids, args.retries)
    for tid, uids in asm_dict.items():
        logger.info("Taxon ID summary\n\tQuery: " +
                    "%s\n\tasm count: %s\n\tUIDs: %s", tid, len(uids), uids)

    # Compile outputs to write class and label files, and a list of
    # skipped downloads (and define a helper tuple for collating skipped
    # genome information)
    classes = []
    labels = []
    skippedlist = []
    Skipped = namedtuple("Skipped",
                         "taxon_id accession organism strain " +
                         "url dltype")

    # Download contigs and hashes for each assembly UID in the list
    # On completion of this loop, each assembly in the list will either be
    # downloaded or skipped (with skipped genome information preserved in
    # skippedlist), and class/label info will be collated, ready for writing
    # to file.
    for tid, uids in asm_dict.items():
        logger.info("Downloading contigs for Taxon ID %s", tid)
        for uid in uids:
            # Obtain eSummary
            logger.info("Get eSummary information for UID %s", uid)
            esummary, filestem = download.get_ncbi_esummary(uid, args.retries)
            uid_class = download.get_ncbi_classification(esummary)

            # Report summary
            outstr = '\n\t'.join(["Species Taxid: %s" % esummary['SpeciesTaxid'],
                                  "TaxID: %s" % esummary['Taxid'],
                                  "Accession: %s" %
                                  esummary['AssemblyAccession'],
                                  "Name: %s" % esummary['AssemblyName'],
                                  "Organism: %s" % uid_class.organism,
                                  "Genus: %s" % uid_class.genus,
                                  "Species: %s" % uid_class.species,
                                  "Strain: %s" % uid_class.strain])
            logger.info("eSummary information:\n\t%s", outstr)
            if args.dryrun:
                logger.warning("(dry-run) skipping download of %s",
                               esummary['AssemblyAccession'])
                continue

            # Obtain URLs, trying the RefSeq filestem first, then GenBank if
            # there's a failure
            ftpstem = "ftp://ftp.ncbi.nlm.nih.gov/genomes/all"
            suffix = "genomic.fna.gz"
            logger.info("Retrieving URLs for %s", filestem)
            # Try RefSeq first
            dlstatus = tools.download_genome_and_hash(filestem, suffix,
                                                      ftpstem, args.outdir,
                                                      args.timeout, logger,
                                                      dltype="RefSeq")
            if dlstatus.skipped:  # RefSeq failed, try GenBank
                skippedlist.append(Skipped(tid, uid,
                                           uid_class.organism,
                                           uid_class.strain,
                                           dlstatus.url, "RefSeq"))
                logger.warning("RefSeq failed. Trying GenBank alternative " +
                               "assembly")
                # Try GenBank assembly
                dlstatus = tools.download_genome_and_hash(filestem, suffix,
                                                          ftpstem, args.outdir,
                                                          args.timeout, logger,
                                                          dltype="GenBank")
                if dlstatus.skipped:
                    skippedlist.append(Skipped(tid, uid,
                                               uid_class.organism,
                                               uid_class.strain,
                                               dlstatus.url, "GenBank"))
                    logger.warning("GenBank failed.")
                    continue  # Move straight on to the next download

            # One of the downloads worked: report information
            logger.info("Downloaded from URL: %s", dlstatus.url)
            logger.info("Wrote assembly to: %s", dlstatus.outfname)
            logger.info("Wrote MD5 hashes to: %s", dlstatus.outfhash)

            # Check hash for the download
            hashstatus = download.check_hash(dlstatus.outfname,
                                             dlstatus.outfhash)
            logger.info("Local MD5 hash: %s", hashstatus.localhash)
            logger.info("NCBI MD5 hash: %s", hashstatus.filehash)
            if hashstatus.passed:
                logger.info("MD5 hash check passed")
            else:
                logger.warning("MD5 hash check failed. " +
                               "Please check and retry.")

            # Extract downloaded files
            ename = os.path.splitext(dlstatus.outfname)[0]
            if os.path.exists(ename) and args.noclobber:
                logger.warning("Output file %s exists, not extracting", ename)
            else:
                logger.info("Extracting archive %s to %s",
                            dlstatus.outfname, ename)
                download.extract_contigs(dlstatus.outfname, ename)

            # Modify sequence ID header if Kraken option active
            if args.kraken:
                logger.warning(
                    "Modifying downloaded sequence for Kraken compatibility")
                seqdata = list(SeqIO.parse(ename, 'fasta'))
                logger.info("Modifying %s", ename)
                for seq in seqdata:
                    seq.id = '|'.join([seq.id, 'kraken:taxid',
                                       esummary['SpeciesTaxid']])
                SeqIO.write(seqdata, ename, 'fasta')

            # Create MD5 hash for the downloaded contigs
            logger.info("Creating local MD5 hash for %s" % ename)
            hashfname = os.path.splitext(ename)[0] + '.md5'
            datahash = download.create_hash(ename)
            logger.info("Writing hash to %s" % hashfname)
            with open(hashfname, "w") as hfh:
                hfh.write('\t'.join([datahash, ename]) + '\n')
            # Make label/class text
            labeltxt, classtxt = download.create_labels(uid_class, filestem,
                                                        datahash)
            classes.append(classtxt)
            labels.append(labeltxt)
            logger.info("Label and class file entries\n" +
                        "\tLabel: %s\n\tClass: %s", labeltxt, classtxt)

    # Write class and label files
    classfname = os.path.join(args.outdir, args.classfname)
    logger.info("Writing classes file to %s", classfname)
    if os.path.exists(classfname) and args.noclobber:
        logger.warning("Class file %s exists, not overwriting", classfname)
    else:
        with open(classfname, "w") as ofh:
            ofh.write('\n'.join(classes) + '\n')

    labelfname = os.path.join(args.outdir, args.labelfname)
    logger.info("Writing labels file to %s", labelfname)
    if os.path.exists(labelfname) and args.noclobber:
        logger.warning("Labels file %s exists, not overwriting", labelfname)
    else:
        with open(labelfname, "w") as ofh:
            ofh.write('\n'.join(labels) + '\n')

    # Report skipped genome list
    if len(skippedlist):
        logger.warning("%d genome downloads were skipped", len(skippedlist))
        for skipped in skippedlist:
            outstr = '\n\t'.join(["taxon id: %s" % skipped.taxon_id,
                                  "accession: %s" % skipped.accession,
                                  "URL: %s" % skipped.url,
                                  "source: %s" % skipped.dltype])
            logger.warning("%s %s:\n\t%s", skipped.organism, skipped.strain,
                           outstr)


# Generate MD5 hashes for each genome in an input directory
def subcmd_index(args, logger):
    """Generate a file with the MD5 hash for each genome in an input directory.

    Identify the genome files in the input directory, and generate a single
    MD5 for each so that <genome>.fna produces <genome>.md5

    Genome files are identified from the file extension.
    """
    # Get list of FASTA files in the input directory
    logger.info("Scanning directory %s for FASTA files", args.indir)
    fpaths = pyani_files.get_fasta_paths(args.indir)
    logger.info('\n'.join(["Found FASTA files:"] +
                          ['\t' + fpath for fpath in fpaths]))

    # Lists of class/label information
    classes = []
    labels = []

    # Create MD5 hash for each file, if needed
    for fpath in fpaths:
        fstem = os.path.splitext(os.path.split(fpath)[-1])[0]
        hashfname = os.path.splitext(fpath)[0] + '.md5'
        if os.path.isfile(hashfname):
            logger.info("%s already indexed (skipping)", fpath)
            if args.force:
                # Parse the file and get the label/class information
                with open(fpath, "r") as sfh:
                    label = list(SeqIO.parse(sfh, 'fasta'))[0].description
                datahash = download.create_hash(fpath)
                labels.append('\t'.join([datahash, fstem, label]))
                classes.append('\t'.join([datahash, fstem, label]))
        else:
            # Write an .md5 hash file
            datahash = download.create_hash(fpath)
            logger.info("Writing hash to %s", hashfname)
            with open(hashfname, "w") as hfh:
                hfh.write('\t'.join([datahash, fpath]) + '\n')

            # Parse the file and get the label/class information
            with open(fpath, "r") as sfh:
                label = list(SeqIO.parse(sfh, 'fasta'))[0].description
            labels.append('\t'.join([datahash, fstem, label]))
            classes.append('\t'.join([datahash, fstem, label]))

    # Write class and label files
    classfname = os.path.join(args.indir, args.classfname)
    logger.info("Writing classes file to %s", classfname)
    if os.path.exists(classfname):
        if not args.force:
            logger.warning("Class file %s exists, not overwriting",
                           classfname)
        else:
            logger.warning("Class file %s exists, but forcing overwrite",
                           classfname)
            with open(classfname, "w") as ofh:
                ofh.write('\n'.join(classes) + '\n')
    else:
        with open(classfname, "w") as ofh:
            ofh.write('\n'.join(classes) + '\n')

    labelfname = os.path.join(args.indir, args.labelfname)
    logger.info("Writing labels file to %s", labelfname)
    if os.path.exists(labelfname):
        if not args.force:
            logger.warning("Labels file %s exists, not overwriting",
                           labelfname)
        else:
            logger.warning("Labels file %s exists, but forcing overwrite",
                           labelfname)
            with open(labelfname, "w") as ofh:
                ofh.write('\n'.join(labels) + '\n')            
    else:
        with open(labelfname, "w") as ofh:
            ofh.write('\n'.join(labels) + '\n')


def subcmd_createdb(args, logger):
    """Create an empty pyani database."""
    # If the database exists, raise an error rather than overwrite
    if os.path.isfile(args.dbpath) and not args.force:
        logger.error("Database %s already exists (exiting)", args.dbpath)
        raise SystemError(1)

    # If the path to the database doesn't exist, create it
    dbdir = os.path.split(args.dbpath)[0]
    if not os.path.isdir(dbdir):
        logger.info("Creating database directory %s", dbdir)
        os.makedirs(dbdir, exist_ok=True)

    # Create the empty database
    logger.info("Creating pyani database at %s", args.dbpath)
    pyani_db.create_db(args.dbpath)


def subcmd_db(args, logger):
    """Perform operations on an existing database."""
    # If the database exists, raise an error rather than overwrite
    if not os.path.isfile(args.dbpath):
        logger.error("Database %s does not exist (exiting)", args.dbpath)
        raise SystemError(1)

    logger.info("Working with pyani database at %s", args.dbpath)
    logger.info("Working with run ID %d", args.run_id)

    # If requested, carry out relabelling of genomes
    if args.relabelfname is not None:
        logger.info("Relabelling genomes from file %s", args.relabelfname)
        pyani_db.relabel_genomes_from_file(args.dbpath,
                                           args.relabelfname, args.run_id, args.force)

    # If requested, change classes of genomes
    if args.reclassfname is not None:
        logger.info("Changing classes of genomes from file %s",
                    args.reclassfname)
        pyani_db.reclass_genomes_from_file(args.dbpath,
                                           args.reclassfname, args.run_id, args.force)


def subcmd_anim(args, logger):
    """Perform ANIm on all genome files in an input directory.

    Finds ANI by the ANIm method, as described in Richter et al (2009)
    Proc Natl Acad Sci USA 106: 19126-19131 doi:10.1073/pnas.0906412106.

    All FASTA format files (selected by suffix) in the input directory
    are compared against each other, pairwise, using NUCmer (whose path must
    be provided).

    For each pairwise comparison, the NUCmer .delta file output is parsed to
    obtain an alignment length and similarity error count for every unique
    region alignment between the two organisms, as represented by
    sequences in the FASTA files. These are processed to calculated aligned
    sequence lengths, average nucleotide identity (ANI) percentages, coverage
    (aligned percentage of whole genome - forward direction), and similarity
    error count for each pairwise comparison.

    The calculated values are deposited in the SQLite3 database being used for
    the analysis.

    For each pairwise comparison the NUCmer output is stored in the output
    directory for long enough to extract summary information, but for each run
    the output is gzip compressed. Once all runs are complete, the outputs
    for each comparison are concatenated into a single gzip archive.
    """
    # Announce the analysis
    logger.info("Running ANIm analysis")

    # Use the provided name or make one for the analysis
    start_time = datetime.datetime.now().isoformat()
    if args.name is None:
        name = '_'.join(["ANIm", start_time])
    else:
        name = args.name

    # Add info for this analysis to the database
    logger.info("Adding analysis information to database %s", args.dbpath)
    run_id = pyani_db.add_run(args.dbpath, "ANIm", args.cmdline,
                              start_time, "started", name)
    logger.info("Current analysis has ID %s in this database", run_id)

    # Identify input files for comparison, and populate the database
    logger.info("Identifying input genome/hash files:")
    infiles = pyani_files.get_fasta_and_hash_paths(args.indir)
    # Get hash string and sequence description for each FASTA/hash pair,
    # and add info to the current database
    for fastafile, hashfile in infiles:
        # Get genome data
        inhash, filecheck = pyani_files.read_hash_string(hashfile)
        indesc = pyani_files.read_fasta_description(fastafile)
        abspath = os.path.abspath(fastafile)
        genome_len = pyani_tools.get_genome_length(abspath)
        outstr = ["FASTA file:\t%s" % abspath,
                  "description:\t%s" % indesc,
                  "hash file:\t%s" % hashfile,
                  "MD5 hash:\t%s" % inhash,
                  "Total length:\t%d" % genome_len]
        logger.info('\t' + '\n\t'.join(outstr))

        # Attempt to add current genome/path combination to database
        logger.info("Adding genome data to database...")
        try:
            genome_id = pyani_db.add_genome(args.dbpath, inhash,
                                            abspath, genome_len, indesc)
        except sqlite3.IntegrityError:  # genome data already in database
            logger.warning("Genome already in database with this " +
                           "hash and path!")
            genome_db = pyani_db.get_genome(args.dbpath, inhash, abspath)
            if len(genome_db) > 1:  # This shouldn't happen
                logger.error("More than one genome with same hash and path")
                logger.error(
                    "This should only happen if the database is corrupt")
                logger.error(
                    "Please investigate the database tables (exiting)")
                raise SystemError(1)
            logger.warning("Using existing genome from database, row %s",
                           genome_db[0][0])
            genome_id = genome_db[0][0]
        logger.debug("Genome row ID: %s", genome_id)

        # Populate the linker table associating each run with the genome IDs
        # for that run
        pyani_db.add_genome_to_run(args.dbpath, run_id, genome_id)

    # Add classes metadata to the database, if provided
    if args.classes is not None:
        logger.info("Collecting class metadata from %s", args.classes)
        classes = pyani_tools.add_dbclasses(args.dbpath, run_id, args.classes)
        logger.debug("Added class IDs: %s", classes)

    # Add labels metadata to the database, if provided
    if args.labels is not None:
        logger.info("Collecting labels metadata from %s", args.labels)
        labels = pyani_tools.add_dblabels(args.dbpath, run_id, args.labels)
        logger.debug("Added label IDs: %s", labels)

    # Generate commandlines for NUCmer analysis and output compression
    logger.info("Generating ANIm command-lines")
    deltadir = os.path.join(os.path.join(args.outdir,
                                         pyani_config.ALIGNDIR['ANIm']))
    logger.info("NUCmer output will be written temporarily to %s", deltadir)

    # Create output directories
    logger.info("Creating output directory %s", deltadir)
    try:
        os.makedirs(deltadir, exist_ok=True)
    except IOError:
        logger.error("Could not create output directory (exiting)")
        logger.error(last_exception())
        raise SystemError(1)

    # Get list of genome IDs for this analysis from the database
    logger.info("Compiling genomes for comparison")
    genome_ids = pyani_db.get_genome_ids_by_run(args.dbpath, run_id)
    logger.debug("Genome IDs for analysis with ID %s:\n\t%s",
                 run_id, genome_ids)

    # Generate all pair combinations of genome IDs
    logger.info(
        "Compiling pairwise comparisons (this can take time for large datasets)")
    comparison_ids = list(combinations(tqdm(genome_ids), 2))
    logger.debug("Complete pairwise comparison list:\n\t%s", comparison_ids)

    # Check for existing comparisons; if one has been done (for the same
    # software package, version, and setting) we remove it from the list
    # of comparisons to be performed, but we add a new entry to the
    # runs_comparisons table.
    # TODO: turn this into a generator or some such?
    nucmer_version = anim.get_version(args.nucmer_exe)

    # Existing entries for the comparison:run link table
    new_link_ids = [(qid, sid) for (qid, sid) in comparison_ids if
                    pyani_db.get_comparison(args.dbpath, qid, sid, "nucmer",
                                            nucmer_version,
                                            maxmatch=args.maxmatch) is
                    not None]
    logger.info("Existing comparisons to be associated with new run:\n\t%s",
                new_link_ids)
    if len(new_link_ids) > 0:
        for (qid, sid) in tqdm(new_link_ids):
            pyani_db.add_comparison_link(args.dbpath, run_id, qid, sid,
                                         "nucmer", nucmer_version,
                                         maxmatch=args.maxmatch)

    # If we are in recovery mode, we are salvaging output from a previous
    # run, and do not necessarily need to rerun all the jobs. In this case,
    # we prepare a list of output files we want to recover from the results
    # in the output directory.
    if args.recovery:
        logger.warning("Entering recovery mode")
        logger.info("\tIn this mode, existing comparison output from %s is reused",
                    deltadir)
        # Obtain collection of expected output files already present in directory
        if args.nofilter:
            suffix = ".delta"
        else:
            suffix = ".filter"
        existingfiles = [fname for fname in os.listdir(deltadir) if
                         os.path.splitext(fname)[-1] == suffix]
        logger.info("Identified %d existing output files", len(existingfiles))

    # New comparisons to be run for this analysis
    # TODO: Can we parallelise this as a function called with multiprocessing?
    logger.info("Excluding comparisons present in database")
    comparison_ids = [(qid, sid) for (qid, sid) in comparison_ids if
                      pyani_db.get_comparison(args.dbpath, qid, sid, "nucmer",
                                              nucmer_version,
                                              maxmatch=args.maxmatch) is None]
    logger.debug("Comparisons still to be performed:\n\t%s", comparison_ids)
    logger.info("Total comparisons to be conducted: %d", len(comparison_ids))

    if not len(comparison_ids):
        logger.info("All comparison results already present in database " +
                    "(skipping comparisons)")
    else:
        # Create list of NUCmer jobs for each comparison still to be
        # performed
        logger.info("Creating NUCmer jobs for ANIm")
        joblist, comparisons = [], []
        jobprefix = "ANINUCmer"
        for idx, (qid, sid) in enumerate(tqdm(comparison_ids)):
            qpath = pyani_db.get_genome_path(args.dbpath, qid)
            spath = pyani_db.get_genome_path(args.dbpath, sid)
            ncmd, dcmd = anim.construct_nucmer_cmdline(qpath, spath,
                                                       args.outdir,
                                                       args.nucmer_exe,
                                                       args.filter_exe,
                                                       args.maxmatch)
            logger.debug("Commands to run:\n\t%s\n\t%s", ncmd, dcmd)
            outprefix = ncmd.split()[3]  # prefix for NUCmer output
            if args.nofilter:
                outfname = outprefix + '.delta'
            else:
                outfname = outprefix + '.filter'
            logger.debug("Expected output file for db: %s", outfname)

            # If we're in recovery mode, we don't want to repeat a computational
            # comparison that already exists, so we check whether the ultimate
            # output is in the set of existing files and, if not, we add the jobs
            # TODO: something faster than a list search (dict or set?)
            # The comparisons collections always gets updated, so that results are
            # added to the database whether they come from recovery mode or are run
            # in this call of the script.
            comparisons.append(Comparison(qid, sid, dcmd, outfname))
            if args.recovery and os.path.split(outfname)[-1] in existingfiles:
                logger.debug("Recovering output from %s, not building job",
                             outfname)
            else:
                logger.debug("Building job")
                # Build jobs
                njob = pyani_jobs.Job("%s_%06d-n" % (jobprefix, idx), ncmd)
                fjob = pyani_jobs.Job("%s_%06d-f" % (jobprefix, idx), dcmd)
                fjob.add_dependency(njob)
                joblist.append(fjob)

        # Pass commands to the appropriate scheduler
        logger.info("Passing %d jobs to scheduler", len(joblist))
        if args.scheduler == 'multiprocessing':
            logger.info("Running jobs with multiprocessing")
            if not args.workers:
                logger.info("(using maximum number of worker threads)")
            else:
                logger.info("(using %d worker threads, if available)",
                            args.workers)
            cumval = run_mp.run_dependency_graph(joblist,
                                                 workers=args.workers,
                                                 logger=logger)
            if 0 < cumval:
                logger.error("At least one NUCmer comparison failed. " +
                             "Please investigate (exiting)")
                raise pyani_tools.PyaniException("Multiprocessing run " +
                                                 "failed in ANIm")
            else:
                logger.info("Multiprocessing run completed without error")
        else:
            logger.info("Running jobs with SGE")
            logger.info("Setting jobarray group size to %d", args.sgegroupsize)
            run_sge.run_dependency_graph(joblist, logger=logger,
                                         jgprefix=args.jobprefix,
                                         sgegroupsize=args.sgegroupsize,
                                         sgeargs=args.sgeargs)

        # Process output and add results to database
        # We have to drop out of threading/multiprocessing to do this: Python's
        # SQLite3 interface doesn't allow sharing connections and cursors
        logger.info("Adding comparison results to database")
        for comp in tqdm(comparisons):
            aln_length, sim_errs = anim.parse_delta(comp.outfile)
            qlen = pyani_db.get_genome_length(args.dbpath, comp.query_id)
            slen = pyani_db.get_genome_length(args.dbpath, comp.subject_id)
            qcov = aln_length / qlen
            scov = aln_length / slen
            pid = 1 - sim_errs / aln_length
            comp_id = pyani_db.add_comparison(args.dbpath, comp.query_id,
                                              comp.subject_id, aln_length,
                                              sim_errs, pid, qcov, scov,
                                              "nucmer", nucmer_version,
                                              maxmatch=args.maxmatch)
            link_id = pyani_db.add_comparison_link(args.dbpath, run_id,
                                                   comp.query_id,
                                                   comp.subject_id,
                                                   "nucmer", nucmer_version,
                                                   maxmatch=args.maxmatch)
            logger.debug("Added ID %s vs %s, as comparison %s (link: %s)",
                         comp.query_id, comp.subject_id, comp_id, link_id)


def subcmd_anib(args, logger):
    """Perform ANIm on all genome files in an input directory."""
    raise NotImplementedError


def subcmd_aniblastall(args, logger):
    """Perform ANIm on all genome files in an input directory."""
    raise NotImplementedError


def subcmd_report(args, logger):
    """Present report on ANI results and/or database contents.

    The report subcommand takes any of several long options that do one of two
    things: 

    1. perform a single action.
    2. set a parameter/format

    These will typically take an output path to a file or directory into which
    the report will be written (whatever form it takes). By default, text
    output is written in plain text format, but for some outputs this can
    be modified by an 'excel' or 'html' format specifier, which writes outputs
    in that format, where possible.
    """
    # Output formats will apply across all tabular data requested
    # Expect comma-separated, and turn them into an iterable
    formats = ['tab']
    if args.formats:
        formats += [fmt.strip() for fmt in args.formats.split(',')]
    formats = list(set(formats))  # remove duplicates
    logger.info("Creating output in formats: %s", formats)

    # Declare which database is being used
    logger.info("Using database: %s", args.dbpath)

    # Report runs in the database
    if args.show_runs:
        outfname = os.path.join(args.outdir, "runs")
        logger.info("Writing table of pyani runs from the database to %s.*",
                    outfname)
        data = pyani_db.get_df_runs(args.dbpath)
        pyani_report.write_dbtable(data, outfname, formats, index='run ID')

    # Report genomes in the database
    if args.show_genomes:
        outfname = os.path.join(args.outdir, "genomes")
        logger.info("Writing table of genomes from the database to %s.*",
                    outfname)
        data = pyani_db.get_df_genomes(args.dbpath)
        pyani_report.write_dbtable(data, outfname, formats, index='genome ID')

    # Report table of all genomes used for each run
    if args.show_runs_genomes:
        outfname = os.path.join(args.outdir, "runs_genomes")
        logger.info("Writing table of pyani runs, with associated genomes " +
                    "to %s.*", outfname)
        data = pyani_db.get_df_run_genomes(args.dbpath)
        pyani_report.write_dbtable(data, outfname, formats)

    # Report table of all runs in which a genome is involved
    if args.show_genomes_runs:
        outfname = os.path.join(args.outdir, "genomes_runs")
        logger.info("Writing table of genomes, with associated pyani runs" +
                    "to %s.*", outfname)
        data = pyani_db.get_df_genome_runs(args.dbpath)
        pyani_report.write_dbtable(data, outfname, formats)

    # Report table of comparison results for the indicated runs
    if args.run_results:
        outfstem = os.path.join(args.outdir, "results")
        run_ids = [run_id.strip() for run_id in args.run_results.split(',')]
        logger.info("Attempting to write results tables for runs: %s",
                    run_ids)
        for run_id in run_ids:
            outfname = '_'.join([outfstem, str(run_id)])
            run_data = pyani_db.get_run(args.dbpath, run_id)
            logger.info("Collecting data for run with ID: %s (%s)", run_id,
                        run_data[5])
            data = pyani_db.get_df_comparisons(args.dbpath, run_id)
            pyani_report.write_dbtable(data, outfname, formats)

    # Report matrices of comparison results for the indicated runs
    # For ANIm, all results other than coverage are symmetric matrices,
    # so we only get results in the forward direction.
    if args.run_matrices:
        outfstem = os.path.join(args.outdir, "matrix")
        run_ids = [run_id.strip() for run_id in args.run_matrices.split(',')]
        logger.info("Attempting to write results matrices for runs: %s",
                    run_ids)
        for run_id in run_ids:
            logger.info("Extracting comparison results for run %s", run_id)
            results = pyani_db.ANIResults(args.dbpath, run_id)
            for matname, args in [('identity', {'colour_num': 0.95}),
                                  ('coverage', {'colour_num': 0.95}),
                                  ('aln_lengths', {}),
                                  ('sim_errors', {}),
                                  ('hadamard', {})]:
                logger.info("Writing %s results", matname)
                outfname = '_'.join([outfstem, matname, str(run_id)])
                pyani_report.write_dbtable(getattr(results, matname),
                                           outfname, formats, show_index=True,
                                           **args)


# Generate plots of pyani outputs
def subcmd_plot(args, logger):
    """Produce graphical output for an analysis.

    This is graphical output for representing the ANI analysis results, and
    takes the form of a heatmap, or heatmap with dendrogram.
    """
    # Announce what's going on to the user
    logger.info("Generating graphical output for analyses")
    logger.info("Writing output to: %s", args.outdir)
    logger.info("Rendering method: %s", args.method)

    # Distribution dictionary of graphics methods
    gmethod = {'mpl': pyani_graphics.heatmap_mpl,
               'seaborn': pyani_graphics.heatmap_seaborn}

    # Work on each run:
    run_ids = [int(run) for run in args.run_id.split(',')]
    logger.info("Generating graphics for runs: %s", run_ids)
    for run_id in run_ids:

        # Get results matrices for the run
        logger.info("Acquiring results for run %d", run_id)
        results = pyani_db.ANIResults(args.dbpath, run_id)

        # Parse output formats
        outfmts = args.formats.split(',')
        logger.info("Requested output formats: %s", outfmts)

        # Generate filestems
        for matname in ['identity', 'coverage', 'aln_lengths', 'sim_errors',
                        'hadamard']:
            df = getattr(results, matname)           # results matrix
            cmap = pyani_config.get_colormap(df, matname)
            for fmt in outfmts:
                outfname = os.path.join(args.outdir,
                                        "matrix_{0}_{1}.{2}".format(matname,
                                                                    run_id,
                                                                    fmt))
                logger.info("Writing graphics to %s", outfname)
                params = pyani_graphics.Params(cmap, results.labels,
                                               results.classes)
                # Draw figure
                gmethod[args.method](df, outfname,
                                     title="matrix_{0}_{1}".format(matname,
                                                                   run_id),
                                     params=params)


# Classify input genomes on basis of ANI coverage and identity output
def subcmd_classify(args, logger):
    """Generate classifications for an analysis."""
    # Tell the user what's going on
    logger.info("Generating classification for ANI run: %s", args.run_id)
    logger.info("Writing output to: %s", args.outdir)
    logger.info("Coverage threshold: %s", args.cov_min)
    logger.info("Initial minimum identity threshold: %s", args.id_min)

    # Get results data for the specified run
    logger.info("Acquiring results for run: %s", args.run_id)
    results = pyani_db.ANIResults(args.dbpath, args.run_id)

    # Generate initial graph on basis of results
    logger.info("Constructing graph from results.")
    initgraph = pyani_classify.build_graph_from_results(results,
                                                        args.cov_min,
                                                        args.id_min)
    logger.info("Returned graph has %d nodes:\n\t%s",
                len(initgraph), '\n\t'.join([n for n in initgraph]))
    logger.info("Initial graph clique information:\n\t%s",
                pyani_classify.analyse_cliques(initgraph))

    # Report all subgraphs, thresholding by identity
    logger.info("Summarising cliques at all identity thresholds:\n\t%s",
                '\n\t'.join(['\t'.join([str(gdata[0]), str(gdata[2])])
                             for gdata in
                             pyani_classify.trimmed_graph_sequence(initgraph)]))

    # Report 'natural breaks' in the identity subgraphs
    logger.info("Identifying 'natural breaks' with no clique-confusion:\n\t%s",
                '\n\t'.join(['\t'.join([str(gdata[0]), str(gdata[2])])
                             for gdata in
                             pyani_classify.unconfused_graphs(initgraph)]))
