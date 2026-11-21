-- Prevent one part number from being present in more than one shared note or
-- parts checklist. The workspace revision lock serializes writes, making this
-- check safe when two coworkers save at the same time.
CREATE OR REPLACE FUNCTION public.jeffery_save_business_entry(
  p_identifier text,
  p_expected_revision bigint,
  p_entry jsonb,
  p_actor text
)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path TO 'pg_catalog', 'jeffery'
AS $function$
DECLARE
  old_row jeffery.business_entries%ROWTYPE;
  new_revision bigint;
  entry_value jsonb;
  conflicting_title text;
BEGIN
  IF p_identifier !~ '^[0-9a-f]{32}$'
     OR jsonb_typeof(p_entry) <> 'object'
     OR p_entry->>'id' <> p_identifier
     OR p_entry->>'kind' NOT IN ('note','order','list')
     OR octet_length(p_entry::text) > 245760
     OR length(coalesce(p_actor,'')) > 128 THEN
    RAISE EXCEPTION 'Invalid shared entry.' USING ERRCODE='22023';
  END IF;

  SELECT * INTO old_row FROM jeffery.business_entries WHERE id = p_identifier;
  IF old_row.id IS NOT NULL AND old_row.revision <> p_expected_revision THEN
    RAISE EXCEPTION 'This entry changed on another computer. Reload it before saving your changes.' USING ERRCODE='40001';
  END IF;
  PERFORM 1 FROM jeffery.business_state WHERE id=1 FOR UPDATE;
  SELECT * INTO old_row FROM jeffery.business_entries WHERE id=p_identifier FOR UPDATE;
  IF coalesce(old_row.revision,0) <> p_expected_revision THEN
    RAISE EXCEPTION 'This entry changed on another computer. Reload it before saving your changes.' USING ERRCODE='40001';
  END IF;
  IF old_row.id IS NOT NULL AND old_row.deleted THEN
    RAISE EXCEPTION 'This shared entry was deleted. Create a new entry instead.' USING ERRCODE='40001';
  END IF;
  IF old_row.id IS NULL AND (SELECT count(*) FROM jeffery.business_entries WHERE not deleted) >= 2000 THEN
    RAISE EXCEPTION 'The shared notebook is full.' USING ERRCODE='23514';
  END IF;

  IF p_entry->>'kind' IN ('note','list') THEN
    SELECT coalesce(nullif(btrim(other.entry_json->>'title'),''),'another note')
    INTO conflicting_title
    FROM jeffery.business_entries AS other
    CROSS JOIN LATERAL jsonb_array_elements(
      CASE WHEN jsonb_typeof(other.entry_json->'checklist') = 'array'
        THEN other.entry_json->'checklist' ELSE '[]'::jsonb END
    ) AS saved_line(value)
    WHERE other.id <> p_identifier
      AND other.deleted = false
      AND other.kind IN ('note','list')
      AND nullif(btrim(saved_line.value->>'part_number'),'') IS NOT NULL
      AND lower(btrim(saved_line.value->>'part_number')) IN (
        SELECT DISTINCT lower(btrim(new_line.value->>'part_number'))
        FROM jsonb_array_elements(
          CASE WHEN jsonb_typeof(p_entry->'checklist') = 'array'
            THEN p_entry->'checklist' ELSE '[]'::jsonb END
        ) AS new_line(value)
        WHERE nullif(btrim(new_line.value->>'part_number'),'') IS NOT NULL
      )
    LIMIT 1;
    IF conflicting_title IS NOT NULL THEN
      RAISE EXCEPTION 'Number cannot be added. That part number is already on note "%". Remove it there first.', conflicting_title
        USING ERRCODE='23505';
    END IF;
  END IF;

  entry_value := p_entry;
  IF old_row.id IS NOT NULL THEN
    entry_value := jsonb_set(entry_value,'{created}',coalesce(old_row.entry_json->'created',entry_value->'created'),true);
  END IF;
  entry_value := jsonb_set(entry_value,'{updated}',to_jsonb(extract(epoch from clock_timestamp())),true);
  UPDATE jeffery.business_state SET revision=revision+1 WHERE id=1 RETURNING revision INTO new_revision;
  INSERT INTO jeffery.business_entries(id,revision,deleted,entry_json,updated_by_username,updated_at,kind)
  VALUES(p_identifier,new_revision,false,entry_value,p_actor,now(),entry_value->>'kind')
  ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,deleted=false,entry_json=excluded.entry_json,
    updated_by_username=excluded.updated_by_username,updated_at=excluded.updated_at,kind=excluded.kind;
  RETURN jsonb_build_object('id',p_identifier,'revision',new_revision,'deleted',false,'entry',entry_value,
    'updated_by_username',p_actor,'updated_at',to_jsonb(now()));
END $function$;
