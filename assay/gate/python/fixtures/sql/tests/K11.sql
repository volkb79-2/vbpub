BEGIN;
DO $$
BEGIN
  INSERT INTO parent (id,label) VALUES (1,'p');
  INSERT INTO child (id,parent_id,owner_id,slot,qty) VALUES (1,1,NULL,5,1);
  BEGIN
    INSERT INTO child (id,parent_id,owner_id,slot,qty) VALUES (2,1,NULL,5,1);
  EXCEPTION WHEN unique_violation THEN RETURN;
  END;
  RAISE EXCEPTION 'K11';
END $$;
ROLLBACK;
