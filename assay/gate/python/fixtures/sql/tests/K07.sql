BEGIN;
DO $$
BEGIN
  BEGIN
    INSERT INTO child (id,parent_id,owner_id,slot,qty) VALUES (1,NULL,NULL,NULL,0);
  EXCEPTION WHEN check_violation THEN RETURN;
  END;
  RAISE EXCEPTION 'K07';
END $$;
ROLLBACK;
