# -*- coding: utf-8 -*-
#
# This file is part of INSPIRE.
# Copyright (C) 2014-2017 CERN.
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

from flask_alembic import Alembic
from sqlalchemy import inspect, text

from invenio_db import db


def test_downgrade(isolated_app):
    alembic = Alembic(isolated_app)
    alembic.upgrade()

    # downgrade 0bc0a6ee1bc0 == downgrade to 2f5368ff6d20

    alembic.downgrade(target='0bc0a6ee1bc0')
    assert 'ix_records_metadata_json_referenced_records_2_0' not in _get_indexes(
        'records_metadata')
    assert 'ix_records_metadata_json_referenced_records' in _get_indexes(
        'records_metadata')

    alembic.downgrade(target='2f5368ff6d20')
    assert 'ix_records_metadata_json_referenced_records' not in _get_indexes(
        'records_metadata')

    # 2f5368ff6d20
    # TODO |Create proper tests for 2f5368ff6d20, eaab22c59b89, f9ea5752e7a5
    # TODO | and 17ff155db70d

    alembic.downgrade(target="eaab22c59b89")

    # eaab22c59b89
    alembic.downgrade(target="f9ea5752e7a5")

    # f9ea5752e7a5

    alembic.downgrade(target="17ff155db70d")

    # 17ff155db70d

    alembic.downgrade(target="402af3fbf68b")

    # 402af3fbf68b

    alembic.downgrade(target='53e8594bc789')

    # 53e8594bc789

    alembic.downgrade(target='d99c70308006')

    assert 'inspire_prod_records' in _get_table_names()
    assert 'inspire_prod_records_recid_seq' in _get_sequences()
    assert 'legacy_records_mirror' not in _get_table_names()
    assert 'legacy_records_mirror_recid_seq' not in _get_sequences()

    # d99c70308006

    alembic.downgrade(target='cb9f81e8251c')
    alembic.downgrade(target='cb5153afd839')

    # cb9f81e8251c & cb5153afd839

    alembic.downgrade(target='fddb3cfe7a9c')

    assert 'idxgindoctype' not in _get_indexes('records_metadata')
    assert 'idxgintitles' not in _get_indexes('records_metadata')
    assert 'idxginjournaltitle' not in _get_indexes('records_metadata')
    assert 'idxgincollections' not in _get_indexes('records_metadata')

    assert 'workflows_record_sources' not in _get_table_names()

    # fddb3cfe7a9c

    alembic.downgrade(target='a82a46d12408')

    assert 'inspire_prod_records' not in _get_table_names()
    assert 'inspire_prod_records_recid_seq' not in _get_sequences()
    assert 'workflows_audit_logging' not in _get_table_names()
    assert 'workflows_audit_logging_id_seq' not in _get_sequences()
    assert 'workflows_pending_record' not in _get_table_names()


def test_upgrade(app):
    alembic = Alembic(app)
    alembic.upgrade()
    alembic.downgrade(target='a82a46d12408')

    # fddb3cfe7a9c

    alembic.upgrade(target='fddb3cfe7a9c')

    assert 'inspire_prod_records' in _get_table_names()
    assert 'inspire_prod_records_recid_seq' in _get_sequences()
    assert 'workflows_audit_logging' in _get_table_names()
    assert 'workflows_audit_logging_id_seq' in _get_sequences()
    assert 'workflows_pending_record' in _get_table_names()

    # cb9f81e8251c

    alembic.upgrade(target='cb9f81e8251c')

    assert 'idxgindoctype' in _get_indexes('records_metadata')
    assert 'idxgintitles' in _get_indexes('records_metadata')
    assert 'idxginjournaltitle' in _get_indexes('records_metadata')
    assert 'idxgincollections' in _get_indexes('records_metadata')

    # cb5153afd839

    alembic.downgrade(target='fddb3cfe7a9c')
    alembic.upgrade(target='cb5153afd839')

    assert 'workflows_record_sources' in _get_table_names()

    # d99c70308006

    alembic.upgrade(target='d99c70308006')

    # 53e8594bc789

    alembic.upgrade(target='53e8594bc789')

    # 402af3fbf68b

    alembic.upgrade(target='402af3fbf68b')

    assert 'inspire_prod_records' not in _get_table_names()
    assert 'inspire_prod_records_recid_seq' not in _get_sequences()
    assert 'legacy_records_mirror' in _get_table_names()
    assert 'legacy_records_mirror_recid_seq' in _get_sequences()

    # 17ff155db70d

    alembic.upgrade(target="17ff155db70d")
    # Not checking as it only adds or modifies columns

    # f9ea5752e7a5

    alembic.upgrade(target="f9ea5752e7a5")
    # Not checking as it only adds or modifies columns

    # eaab22c59b89
    alembic.upgrade(target="eaab22c59b89")

    # 2f5368ff6d20
    alembic.upgrade(target="2f5368ff6d20")
    # TODO Create proper tests for 2f5368ff6d20, eaab22c59b89, f9ea5752e7a5,
    # 17ff155db70d

    # 0bc0a6ee1bc0

    alembic.upgrade(target='0bc0a6ee1bc0')

    assert 'ix_records_metadata_json_referenced_records' in _get_indexes(
        'records_metadata')

    alembic.upgrade(target='2dd443feeb63')
    assert 'ix_records_metadata_json_referenced_records_2_0' in _get_indexes(
        'records_metadata')
    assert 'ix_records_metadata_json_referenced_records' not in _get_indexes(
        'records_metadata')


def _get_indexes(tablename):
    query = text('''
        SELECT indexname
        FROM pg_indexes
        WHERE tablename=:tablename
    ''').bindparams(tablename=tablename)

    return [el.indexname for el in db.session.execute(query)]


def _get_sequences():
    query = text('''
        SELECT relname
        FROM pg_class
        WHERE relkind='S'
    ''')

    return [el.relname for el in db.session.execute(query)]


def _get_table_names():
    inspector = inspect(db.engine)

    return inspector.get_table_names()
