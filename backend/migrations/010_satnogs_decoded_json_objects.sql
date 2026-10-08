-- Repair satnogs_observations.decoded_json rows stored as JSON *strings*.
--
-- The fetcher json.dumps'ed the metadata before asyncpg's JSONB codec
-- encoded it again, so each value was a string containing JSON instead of
-- an object. Unwrap them; only values that are serialized objects (start
-- with '{') are touched, which is all the fetcher ever wrote.

UPDATE satnogs_observations
   SET decoded_json = (decoded_json #>> '{}')::jsonb
 WHERE jsonb_typeof(decoded_json) = 'string'
   AND left(decoded_json #>> '{}', 1) = '{';
