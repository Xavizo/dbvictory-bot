"""Navigation identity excludes presentation and combat-only draft edits."""
import hashlib
import json


def navigation_revision(draft):
    shape=[draft.get('id'),draft.get('points'),draft.get('links',[]),draft.get('loop',True)]
    return hashlib.sha256(json.dumps(shape,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('utf-8')).hexdigest()


def same_route(left,right):
    return bool(left and right and navigation_revision(left)==navigation_revision(right))
