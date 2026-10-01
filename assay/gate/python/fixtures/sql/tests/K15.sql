BEGIN;
DO $$
BEGIN
  BEGIN
    INSERT INTO child (id,parent_id,owner_id,slot,qty) VALUES (1,999,NULL,NULL,1);
  EXCEPTION WHEN foreign_key_violation THEN RETURN;
  END;
  RAISE EXCEPTION 'K15';
END $$;
ROLLBACK;
