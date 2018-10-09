# -*- coding: utf-8 -*-
#
# This file is part of INSPIRE.
# Copyright (C) 2014-2018 CERN.
#
# INSPIRE is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# INSPIRE is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with INSPIRE. If not, see <http://www.gnu.org/licenses/>.
#
# In applying this license, CERN does not waive the privileges and immunities
# granted to it by virtue of its status as an Intergovernmental Organization
# or submit itself to any jurisdiction.

from __future__ import absolute_import, division, print_function

from time import sleep

import click
import click_spinner
import json
import pprint

from invenio_db import db
from invenio_files_rest.models import ObjectVersion
from invenio_pidstore.models import PersistentIdentifier, PIDStatus
from flask import current_app
from flask.cli import with_appcontext
from invenio_records_files.models import RecordsBuckets

from .checkers import check_unlinked_references
from .tasks import batch_reindex

from invenio_records.models import RecordMetadata

from sqlalchemy import (
    String,
    cast,
    type_coerce,
    or_,
    not_
)

from sqlalchemy.dialects.postgresql import JSONB


@click.group()
def check():
    """Commands to perform checks on records"""


@check.command()
@click.argument('doi_file_name', type=click.File('w', encoding='utf-8'), default='missing_cited_dois.txt')
@click.argument('arxiv_file_name', type=click.File('w', encoding='utf-8'), default='missing_cited_arxiv_eprints.txt')
@with_appcontext
def unlinked_references(doi_file_name, arxiv_file_name):
    """Find often cited literature that is not on INSPIRE.

    It generates two files with a list of DOI/arxiv ids respectively,
    in which each line has the respective identifier, folowed by two numbers,
    representing the amount of times the literature has been cited
    by a core and a non-core article respectively.
    The lists are ordered by an internal measure of relevance."""
    with click_spinner.spinner():
        click.echo('Looking up unlinked references...')
        result_doi, result_arxiv = check_unlinked_references()

    click.echo('Done!')
    click.echo(u'Output written to "{}" and "{}"'.format(doi_file_name.name, arxiv_file_name.name))

    for item in result_doi:
        doi_file_name.write(u'{i[0]}: {i[1]}\n'.format(i=item))

    for item in result_arxiv:
        arxiv_file_name.write(u'{i[0]}: {i[1]}\n'.format(i=item))


def next_batch(iterator, batch_size):
    """Get first batch_size elements from the iterable, or remaining if less.

    :param iterator: the iterator for the iterable
    :param batch_size: size of the requested batch
    :return: batch (list)
    """
    batch = []

    try:
        for idx in range(batch_size):
            batch.append(next(iterator))
    except StopIteration:
        pass

    return batch


@click.command()
@click.option('--yes-i-know', is_flag=True)
@click.option('-t', '--pid-type', multiple=True, required=True)
@click.option('-s', '--batch-size', default=200)
@click.option('-q', '--queue-name', default='indexer_task')
@with_appcontext
def simpleindex(yes_i_know, pid_type, batch_size, queue_name):
    """Bulk reindex all records in a parallel manner.

    :param yes_i_know: if True, skip confirmation screen
    :param pid_type: array of PID types, allowed: lit, con, exp, jou, aut, job, ins
    :param batch_size: number of documents per batch sent to workers.
    :param queue_name: name of the celery queue
    """
    if not yes_i_know:
        click.confirm(
            'Do you really want to reindex the record?',
            abort=True,
        )

    click.secho('Sending record UUIDs to the indexing queue...', fg='green')

    query = (
        db.session.query(PersistentIdentifier.object_uuid).join(RecordMetadata, type_coerce(PersistentIdentifier.object_uuid, String) == type_coerce(RecordMetadata.id, String))
        .filter(
            PersistentIdentifier.pid_type.in_(pid_type),
            PersistentIdentifier.object_type == 'rec',
            PersistentIdentifier.status == PIDStatus.REGISTERED,
            or_(
                not_(
                    type_coerce(RecordMetadata.json, JSONB).has_key('deleted')
                ),
                RecordMetadata.json["deleted"] == cast(False, JSONB)
            )
            # noqa: F401
        )
    )

    request_timeout = current_app.config.get('INDEXER_BULK_REQUEST_TIMEOUT')
    all_tasks = []
    records_per_tasks = {}

    with click.progressbar(
        query.yield_per(2000),
        length=query.count(),
        label='Scheduling indexing tasks'
    ) as items:
        batch = next_batch(items, batch_size)

        while batch:
            uuids = [str(item[0]) for item in batch]
            indexer_task = batch_reindex.apply_async(
                kwargs={
                    'uuids': uuids,
                    'request_timeout': request_timeout,
                },
                queue=queue_name,
            )

            records_per_tasks[indexer_task.id] = uuids
            all_tasks.append(indexer_task)
            batch = next_batch(items, batch_size)

    click.secho('Created {} tasks.'.format(len(all_tasks)), fg='green')

    with click.progressbar(
        length=len(all_tasks),
        label='Indexing records'
    ) as progressbar:
        def _finished_tasks_count():
            return len(filter(lambda task: task.ready(), all_tasks))

        while len(all_tasks) != _finished_tasks_count():
            sleep(0.5)
            # this is so click doesn't divide by 0:
            progressbar.pos = _finished_tasks_count() or 1
            progressbar.update(0)

    failures = []
    successes = 0
    batch_errors = []

    for task in all_tasks:
        result = task.result
        if task.failed():
            batch_errors.append({
                'task_id': task.id,
                'error': result,
            })
        else:
            successes += result['success']
            failures += result['failures']

    color = 'red' if failures or batch_errors else 'green'
    click.secho(
        'Reindexing finished: {} failed, {} succeeded, additionally {} batches errored.'.format(
            len(failures),
            successes,
            len(batch_errors),
        ),
        fg=color,
    )

    failures_log_path = '/tmp/records_index_failures.log'
    errors_log_path = '/tmp/records_index_errors.log'

    if failures:
        failures_json = []
        for failure in failures:
            try:
                failures_json.append({
                    'id': failure['index']['_id'],
                    'error': failure['index']['error'],
                })
            except Exception:
                failures_json.append({
                    'error': repr(failure),
                })
        with open(failures_log_path, 'w') as log:
            json.dump(failures_json, log)

        click.secho('You can see the index failures in %s' % failures_log_path)

    if batch_errors:
        errors_json = []
        for error in batch_errors:
            task_id = error['task_id']
            failed_uuids = records_per_tasks[task_id]
            errors_json.append({
                'ids': failed_uuids,
                'error': repr(error['error']),
            })
        with open(errors_log_path, 'w') as log:
            json.dump(errors_json, log)

        click.secho('You can see the errors in %s' % errors_log_path)


@click.command()
@click.option('--remove-no-control-number', is_flag=True)
@click.option('--remove-duplicates', is_flag=True)
@click.option('--remove-not-in-pidstore', is_flag=True)
@click.option('-c', '--print-without-control-number', is_flag=True)
@click.option('-p', '--print-pid-not-in-pidstore', is_flag=True)
@click.option('-d', '--print-duplicates', is_flag=True)
@with_appcontext
def handle_duplicates(remove_no_control_number, remove_duplicates,
                      print_without_control_number, print_pid_not_in_pidstore,
                      print_duplicates, remove_not_in_pidstore):
    query = RecordMetadata.query.with_entities(
            RecordMetadata.id,
            RecordMetadata.json['control_number']
    ).outerjoin(
        PersistentIdentifier,
        PersistentIdentifier.object_uuid == RecordMetadata.id
    ).filter(
        PersistentIdentifier.object_uuid == None  # noqa: E711
    )
    out = query.all()

    recs_no_control_number = []
    recs_no_in_pid_store = []
    others = []

    click.echo("Processing %s records:" % len(out))
    with click.progressbar(out) as data:
        for rec in data:
            # cn = RecordMetadata.query.get(rec).json.get('control_number')
            cn = rec[1]
            if not cn:
                recs_no_control_number.append(rec)
            elif not PersistentIdentifier.query.filter(
                    PersistentIdentifier.pid_value == str(cn)).one_or_none():
                recs_no_in_pid_store.append(rec)
            else:
                others.append(rec)

    click.secho("Found %s records not in PID store" % len(out))
    click.secho("\t%s records without control number" % len(recs_no_control_number))
    click.secho("\t%s records with their PID not in pidstore" % (
        len(recs_no_in_pid_store)))
    click.secho("\t%s records which are duplicates of records in pid store" % (
        len(others)))

    if print_without_control_number:
        click.secho("Records which are missing control number:\n%s" % (
            pprint.pformat(recs_no_control_number)))
    if print_pid_not_in_pidstore:
        click.secho("Records missing in PID store:\n%s" % (
            pprint.pformat(recs_no_in_pid_store)))
    if print_duplicates:
        click.secho("Duplicates:\n%s" % (pprint.pformat(others)))

    if remove_no_control_number:
        click.secho("Removing records which do not have control number (%s)" % (
            len(recs_no_control_number)))
        removed_records, _, _ = _remove_records(recs_no_control_number)
        click.secho("Removed %s out of %s records which did not have." % (
            removed_records, len(recs_no_control_number)))

    if remove_not_in_pidstore:
        click.secho("Removing records which PID is not in PID store but they are no duplicates (%s)" % (
            len(recs_no_in_pid_store)))
        removed_records, _, _ = _remove_records(recs_no_in_pid_store)
        click.secho("Removed %s out of %s records which PID was missing from PID store." % (
            removed_records, len(recs_no_in_pid_store)))

    if remove_duplicates:
        click.secho("Removing records which looks to be duplicates (%s)" % (
            len(others)))
        removed_records, _, _ = _remove_records(others)
        click.secho("Removed %s out of %s records which looks to be duplicates." % (
            removed_records, len(others)))
    db.session.commit()


def _remove_records(records_ids):
    """ This method is only a helper for removal of records which are not in PID store.
        If you will use it for records which are in PID store it will fail as it not removes data from PID store itself.
    Args:
        records_ids: List of tuples with record.id and record.control_number

    Returns: Tuple with information how many records, buckets and objects was removed

    """
    records_ids = [str(r[0]) for r in records_ids]
    recs = RecordMetadata.query.filter(
        RecordMetadata.id.in_(records_ids)
    )
    recs_buckets = RecordsBuckets.query.filter(
        RecordsBuckets.record_id.in_(records_ids)
    )

    # as in_ is not working for relationships...
    buckets_ids = [str(bucket.bucket_id) for bucket in recs_buckets]
    objects = ObjectVersion.query.filter(
        ObjectVersion.bucket_id.in_(buckets_ids)
    )

    removed_objects = objects.delete(synchronize_session=False)
    removed_buckets = recs_buckets.delete(synchronize_session=False)
    removed_records = recs.delete(synchronize_session=False)

    return(removed_records, removed_buckets, removed_objects)
