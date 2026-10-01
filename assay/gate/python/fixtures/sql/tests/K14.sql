BEGIN;
DO $$
BEGIN
  INSERT INTO parent (id,label) VALUES (1,'p');
  INSERT INTO child (id,parent_id,owner_id,slot,qty) VALUES (1,NULL,1,NULL,200);
  BEGIN
    INSERT INTO child (id,parent_id,owner_id,slot,qty) VALUES (2,NULL,1,NULL,300);
  EXCEPTION WHEN unique_violation THEN RETURN;
  END;
  RAISE EXCEPTION 'K14';
END $$;
ROLLBACK;
