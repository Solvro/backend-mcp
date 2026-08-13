RATE_LIMITING_SCRIPT: str = """
    local key = KEYS[1]
    local now = tonumber(ARGV[1])
    local window = tonumber(ARGV[2])
    local limit = tonumber(ARGV[3])
    local member = ARGV[4]

    redis.call("ZREMRANGEBYSCORE", key, 0, now - window)

    local count = redis.call("ZCARD", key)

    local oldest_ms = now
    local oldest = redis.call("ZRANGE", key, 0, 0, "WITHSCORES")
    if oldest[2] then
        oldest_ms = tonumber(oldest[2])
    end

    if count >= limit then
        return {0, count, oldest_ms}
    end

    redis.call("ZADD", key, now, member)
    redis.call("PEXPIRE", key, window)

    return {1, count + 1, oldest_ms}
"""
