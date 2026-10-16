"""Shared diagnostic/workflow admission. Cost is reserved before any provider egress."""

import hashlib
import hmac
from uuid import UUID

from app.core.config import Settings

GATE = """
local cost=tonumber(ARGV[5] or '1')
if redis.call('EXISTS',KEYS[2]) == 1 then return -1 end
if tonumber(redis.call('GET',KEYS[1]) or '0') + cost > tonumber(ARGV[1]) then return -2 end
local count=redis.call('INCRBY',KEYS[1],cost)
if count == cost then redis.call('EXPIRE',KEYS[1],ARGV[2]) end
redis.call('SET',KEYS[2],ARGV[3],'EX',ARGV[4])
return 1
"""
REFRESH = """if redis.call('GET',KEYS[1]) == ARGV[1] then redis.call('EXPIRE',KEYS[1],ARGV[2]); return 1 end return 0"""
RELEASE = (
    """if redis.call('GET',KEYS[1]) == ARGV[1] then return redis.call('DEL',KEYS[1]) end return 0"""
)


def limiter_keys(settings: Settings, organization_id: UUID) -> tuple[str, str]:
    digest = hmac.new(
        settings.jwt_secret.get_secret_value().encode(),
        str(organization_id).encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"ai:probe:{{{digest}}}:count", f"ai:probe:{{{digest}}}:lease"
