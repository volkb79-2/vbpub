BEGIN;
DO $$
BEGIN
  INSERT INTO parent (id,label) VALUES (1,'p');
  INSERT INTO child (id,parent_id,owner_id,slot,qty) VALUES (1,NULL,1,NULL,1);
  BEGIN
    DELETE FROM parent WHERE id=1;
  EXCEPTION WHEN foreign_key_violation OR restrict_violation THEN RETURN;
  END;
  RAISE EXCEPTION 'K18';
END $$;
ROLLBACK;
