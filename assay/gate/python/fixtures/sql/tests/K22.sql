BEGIN;
DO $$
BEGIN
  INSERT INTO parent (id,label) VALUES (1,'p');
  INSERT INTO child (id,parent_id,owner_id,slot,qty) VALUES (1,1,NULL,NULL,1);
  IF NOT ((SELECT count(*) FROM audit)=1) THEN RAISE EXCEPTION 'K22'; END IF;
END $$;
ROLLBACK;
