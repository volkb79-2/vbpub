BEGIN;
DO $$
DECLARE caught_sqlstate text;
BEGIN
  INSERT INTO parent (id,label) VALUES (1,'p');
  INSERT INTO child (id,parent_id,owner_id,slot,qty) VALUES (1,1,NULL,NULL,1);
  BEGIN
    DELETE FROM parent WHERE id=1;
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS caught_sqlstate = RETURNED_SQLSTATE;
    IF caught_sqlstate <> '23503' THEN
      RAISE EXCEPTION 'K19 expected SQLSTATE 23503, got %', caught_sqlstate;
    END IF;
    RAISE NOTICE 'ASSAY_SQLSTATE=23503';
    RETURN;
  END;
  RAISE EXCEPTION 'K19';
END $$;
ROLLBACK;
