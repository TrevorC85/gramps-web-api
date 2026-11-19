#
# Gramps Web API - A RESTful API for the Gramps genealogy program
#
# Copyright (C) 2020      David Straub
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#

"""Full-text search endpoint."""

from __future__ import annotations

import re
from typing import Dict, Optional

from flask import Response
from flask_jwt_extended import get_jwt_identity
from gramps.gen.db.base import DbReadBase
from gramps.gen.errors import HandleError
from gramps.gen.lib.primaryobj import BasicPrimaryObject as GrampsObject
from gramps.gen.utils.grampslocale import GrampsLocale
from marshmallow import Schema
from unidecode import unidecode
from webargs import fields, validate

from ...auth.const import PERM_TRIGGER_REINDEX, PERM_VIEW_PRIVATE
from ...const import PRIMARY_GRAMPS_OBJECTS
from ..auth import has_permissions, require_permissions
from ..blueprint import api_blueprint
from ..access import RestrictedDb
from ..search import get_search_indexer
from ..search.query import parse_query
from ..search.text import tokenize
from ..tasks import (
    AsyncResult,
    make_task_response,
    run_task,
    search_reindex_full,
    search_reindex_incremental,
)
from ..util import (
    get_db_handle,
    get_locale_for_language,
    get_tree_from_jwt_or_fail,
)
from . import ProtectedResource
from .emit import GrampsJSONEncoder
from .schemas import SearchResultSchema
from .util import (
    abort_with_message,
    get_citation_profile_for_object,
    get_event_profile_for_object,
    get_family_profile_for_object,
    get_media_profile_for_object,
    get_person_profile_for_object,
    get_place_profile_for_object,
    get_repository_profile_for_object,
)


class SearchQueryArgs(Schema):
    """Query arguments for GET /search/."""

    locale = fields.Str(
        load_default=None,
        validate=validate.Length(min=1, max=5),
        metadata={
            "description": "Language code of the locale to use where applicable. Must be a valid code from the available translations."
        },
    )
    query = fields.Str(
        required=True,
        validate=validate.Length(min=1),
        metadata={"description": "The search string."},
    )
    semantic = fields.Boolean(
        load_default=False,
        metadata={
            "description": (
                "Ignored. Semantic search was removed in version 3.25.0;"
                " the parameter is accepted for backward compatibility."
            )
        },
    )
    page = fields.Int(
        load_default=1,
        validate=validate.Range(min=1),
        metadata={"description": "Page number of the result subset to return."},
    )
    pagesize = fields.Int(
        load_default=20,
        validate=validate.Range(min=1),
        metadata={"description": "Number of search results per page."},
    )
    sort = fields.DelimitedList(
        fields.Str(validate=validate.Length(min=1)),
        metadata={
            "description": "Comma-delimited sort keys for search results. Available: change, type. Prefix with '-' for descending."
        },
    )
    precision = fields.Integer(
        load_default=3,
        validate=validate.Range(min=1, max=3),
        metadata={
            "description": "Number of significant time components in age/span"
            " strings when profile is used: 1=year only, 2=year+month,"
            " 3=year+month+day."
        },
    )
    profile = fields.DelimitedList(
        fields.Str(validate=validate.Length(min=1)),
        validate=validate.ContainsOnly(
            choices=["all", "self", "families", "events", "age", "span"]
        ),
        metadata={
            "description": "Comma-delimited profile sections to include for matching objects. Possible values: all, age, self, span, events, families, references."
        },
    )
    strip = fields.Boolean(
        load_default=False,
        metadata={
            "description": "If true, strip keys with empty values from the response."
        },
    )
    type = fields.DelimitedList(
        fields.Str(validate=validate.Length(min=1)),
        validate=validate.ContainsOnly(
            choices=[t.lower() for t in PRIMARY_GRAMPS_OBJECTS]
        ),
        metadata={
            "description": "Comma-delimited list of object types to include (e.g. 'person,family,source')."
        },
    )
    change = fields.Str(
        validate=validate.Length(min=2),
        metadata={
            "description": "ISO-8601 timestamp filter (prefix with '>' or '<') to filter by last-change date."
        },
    )


class SearchResource(GrampsJSONEncoder, ProtectedResource):
    """Fulltext search resource."""

    @property
    def db_handle(self) -> DbReadBase:
        """Get the database instance."""
        return get_db_handle()

    def get_object_from_handle(
        self, handle: str, class_name: str, args: Dict, locale: GrampsLocale
    ) -> GrampsObject:
        """Get the object given a Gramps handle."""
        query_method = self.db_handle.method("get_%s_from_handle", class_name)
        assert query_method is not None  # type checker
        obj = query_method(handle)
        if obj is None:
            raise HandleError(f"Object not found for handle {handle}")
        if "profile" in args:
            if class_name == "person":
                obj.profile = get_person_profile_for_object(
                    self.db_handle,
                    obj,
                    args["profile"],
                    locale=locale,
                    name_format=args.get("name_format"),
                    precision=args.get("precision", 3),
                )
            elif class_name == "family":
                obj.profile = get_family_profile_for_object(
                    self.db_handle,
                    obj,
                    args["profile"],
                    locale=locale,
                    name_format=args.get("name_format"),
                    precision=args.get("precision", 3),
                )
            elif class_name == "event":
                obj.profile = get_event_profile_for_object(
                    self.db_handle,
                    obj,
                    args["profile"],
                    locale=locale,
                    name_format=args.get("name_format"),
                    precision=args.get("precision", 3),
                )
            elif class_name == "citation":
                obj.profile = get_citation_profile_for_object(
                    self.db_handle, obj, args["profile"], locale=locale
                )
            elif class_name == "place":
                obj.profile = get_place_profile_for_object(
                    self.db_handle, obj, locale=locale
                )
            elif class_name == "media":
                obj.profile = get_media_profile_for_object(
                    self.db_handle, obj, args["profile"], locale=locale
                )
            elif class_name == "repository":
                obj.profile = get_repository_profile_for_object(
                    self.db_handle, obj, args["profile"], locale=locale
                )

        return obj

    @api_blueprint.response(200, SearchResultSchema(many=True))
    @api_blueprint.arguments(SearchQueryArgs, location="query")
    def get(self, args: Dict):
        """Get search result."""
        tree = get_tree_from_jwt_or_fail()
        try:
            searcher = get_search_indexer(tree)
        except ValueError as exc:
            abort_with_message(503, str(exc))
        if args.get("change"):
            match = re.match(r"^(<=|>=|<|>)(\d+(\.\d+)?)$", args["change"])
            if match:
                change_op: Optional[str] = match.group(1)
                change_value: Optional[float] = float(match.group(2))
            else:
                abort_with_message(422, "change parameter has invalid format")
        else:
            change_op = None
            change_value = None

        locale = get_locale_for_language(args["locale"], default=True)
        db_handle = self.db_handle
        if isinstance(db_handle, RestrictedDb) and db_handle.policy.redacts_living:
            total, hits = _search_with_living_stubs(
                searcher, db_handle, args, change_op, change_value, locale
            )
            for hit in hits:
                hit["object"] = self.get_object_from_handle(
                    handle=hit["handle"],
                    class_name=hit["object_type"],
                    args=args,
                    locale=locale,
                )
            return self.response(200, payload=hits, args=args, total_items=total)
        total, hits = searcher.search(
            query=args["query"],
            page=args["page"],
            pagesize=args["pagesize"],
            # search in private records if allowed to
            include_private=has_permissions([PERM_VIEW_PRIVATE]),
            sort=args.get("sort"),
            object_types=args.get("type") or None,
            change_op=change_op,
            change_value=change_value,
            locale=locale,
        )
        if hits:
            for hit in hits:
                try:
                    hit["object"] = self.get_object_from_handle(
                        handle=hit["handle"],
                        class_name=hit["object_type"],
                        args=args,
                        locale=locale,
                    )
                except HandleError:
                    pass
            # filter out hits without object (i.e. if handle failed)
            hits = [hit for hit in hits if "object" in hit]
        return self.response(200, payload=hits or [], args=args, total_items=total)


# Collins family fork: search for users who see living people as first-name
# stubs (LIVING_FIRST_NAME_ONLY). Stubs are private in the search index, so
# they are matched here on the given name shown, and only on that. All index
# hits are read and the hidden ones dropped before paging, so that the total
# counts only what is returned.

_OBJECT_CLASS = {name.lower(): name for name in PRIMARY_GRAMPS_OBJECTS}
_CHANGE_OPS = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}


def stub_matches(query: Optional[str], given_name: str) -> bool:
    """Whether a search query matches a stub's given name, like the index
    would (all clauses, any term per clause, whole tokens unless prefix)."""
    if not query or query.strip() == "*":
        return True
    clauses = parse_query(query)
    if not clauses:
        return False
    variants = [tokenize(given_name), tokenize(unidecode(given_name))]

    def term_matches(term, tokens) -> bool:
        size = len(term.tokens)
        for start in range(len(tokens) - size + 1):
            window = tokens[start : start + size]
            if list(window[:-1]) != list(term.tokens[:-1]):
                continue
            last, wanted = window[-1], term.tokens[-1]
            if last == wanted or (term.prefix and last.startswith(wanted)):
                return True
        return False

    return all(
        any(term_matches(term, tokens) for term in clause for tokens in variants)
        for clause in clauses
    )


def _search_with_living_stubs(searcher, db_handle, args, change_op, change_value, locale):
    """Return (total, page of hits) for a restricted user who sees stubs."""
    common = {
        "query": args["query"],
        "include_private": False,
        "sort": args.get("sort"),
        "object_types": args.get("type") or None,
        "change_op": change_op,
        "change_value": change_value,
        "locale": locale,
    }
    count, _ = searcher.search(page=1, pagesize=1, **common)
    raw = searcher.search(page=1, pagesize=count, **common)[1] if count else []
    hits = [
        hit
        for hit in raw
        if hit["object_type"] in _OBJECT_CLASS
        and db_handle.is_visible(_OBJECT_CLASS[hit["object_type"]], hit["handle"])
    ]
    stubs = []
    types = args.get("type") or None
    if types is None or "person" in types:
        top = max((hit.get("score") or 0 for hit in hits), default=1.0) or 1.0
        for handle in sorted(db_handle.living_stub_handles()):
            person = db_handle.get_person_from_handle(handle)
            if person is None:
                continue
            if change_op and not _CHANGE_OPS[change_op](0.0, change_value):
                continue
            if stub_matches(args["query"], person.primary_name.get_first_name()):
                stubs.append({"handle": handle, "object_type": "person", "score": top})
    sort = [key for key in (args.get("sort") or []) if key.lstrip("+-") == "change"]
    if sort and not sort[0].startswith("-"):
        # stubs have no change date (0): oldest first
        merged = stubs + hits
    elif sort:
        merged = hits + stubs
    else:
        # direct name matches first, like the best index hits
        merged = stubs + hits
    start = (args["page"] - 1) * args["pagesize"]
    return len(merged), merged[start : start + args["pagesize"]]


class SearchIndexQueryArgs(Schema):
    """Query arguments for POST /search/index/."""

    full = fields.Boolean(
        load_default=False,
        metadata={
            "description": "If true, perform a full reindex; otherwise incremental."
        },
    )
    semantic = fields.Boolean(
        load_default=False,
        metadata={
            "description": (
                "Ignored. Semantic search was removed in version 3.25.0;"
                " the parameter is accepted for backward compatibility."
            )
        },
    )


class SearchIndexResource(ProtectedResource):
    """Resource to trigger a search reindex."""

    @api_blueprint.arguments(SearchIndexQueryArgs, location="query")
    def post(self, args: Dict):
        """Trigger a reindex."""
        require_permissions([PERM_TRIGGER_REINDEX])
        tree = get_tree_from_jwt_or_fail()
        user_id = get_jwt_identity()
        task_func = search_reindex_full if args["full"] else search_reindex_incremental
        task = run_task(task_func, tree=tree, user_id=user_id)
        if isinstance(task, AsyncResult):
            return make_task_response(task)
        return Response(status=201)
