BEGIN;
DO $$
BEGIN
  BEGIN
    INSERT INTO child (id,parent_id,owner_id,slot,qty) VALUES (1,NULL,999,NULL,1);
  EXCEPTION WHEN foreign_key_violation THEN RETURN;
  END;
  RAISE EXCEPTION 'K16';
END $$;
ROLLBACK;
