BEGIN;
DO $$
BEGIN
  BEGIN
    INSERT INTO child (id,parent_id,owner_id,slot,qty) VALUES (1,NULL,NULL,NULL,NULL);
  EXCEPTION WHEN not_null_violation THEN RETURN;
  END;
  RAISE EXCEPTION 'K03';
END $$;
ROLLBACK;
