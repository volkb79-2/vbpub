(* Behavioural fixture for the original Augeaswrap Pam_env overlay lens. *)

module Test_Pam_env =

  let conf = "# login environment
REMOTEHOST DEFAULT=localhost OVERRIDE=@{PAM_RHOST}
LESS DEFAULT=\"M q e h15 z23 b80\"
LIST DEFAULT=a,b,c

PATH DEFAULT=${HOME}/bin:/usr/local/bin:/bin
URL DEFAULT=a=b
EMPTY DEFAULT=
"

  let conf_tree =
    { "#comment" = "login environment" }
    { "1"
      { "kind" = "conf" }
      { "name" = "REMOTEHOST" }
      { "default" = "localhost" }
      { "override" = "@{PAM_RHOST}" } }
    { "2"
      { "kind" = "conf" }
      { "name" = "LESS" }
      { "default" = "\"M q e h15 z23 b80\"" } }
    { "3"
      { "kind" = "conf" }
      { "name" = "LIST" }
      { "default" = "a,b,c" } }
    { }
    { "4"
      { "kind" = "conf" }
      { "name" = "PATH" }
      { "default" = "${HOME}/bin:/usr/local/bin:/bin" } }
    { "5"
      { "kind" = "conf" }
      { "name" = "URL" }
      { "default" = "a=b" } }
    { "6"
      { "kind" = "conf" }
      { "name" = "EMPTY" }
      { "default" = "" } }

  test Pam_env.lns get conf = conf_tree

  test Pam_env.lns put conf after
    set "/1/default" "remote.local" ;
    set "/3/override" "none" ;
    rm "/4" ;
    set "/5/default" "left=right" ;
    set "/6/default" ""
  = "# login environment
REMOTEHOST DEFAULT=remote.local OVERRIDE=@{PAM_RHOST}
LESS DEFAULT=\"M q e h15 z23 b80\"
LIST DEFAULT=a,b,c OVERRIDE=none
URL DEFAULT=left=right
EMPTY DEFAULT=

"

  let environment = "# service environment
SITE_MODE=production
GREETING=\"hello world\"
CSV_VALUES=one,two,three
EMPTY_VALUE=\"\"
URL=https://example.test/path?a=b
EMPTY=
EXPORTED=factory
QUOTED='single quoted'
"

  let environment_tree =
    { "#comment" = "service environment" }
    { "1" { "kind" = "environment" } { "name" = "SITE_MODE" } { "value" = "production" } }
    { "2" { "kind" = "environment" } { "name" = "GREETING" } { "value" = "\"hello world\"" } }
    { "3" { "kind" = "environment" } { "name" = "CSV_VALUES" } { "value" = "one,two,three" } }
    { "4" { "kind" = "environment" } { "name" = "EMPTY_VALUE" } { "value" = "\"\"" } }
    { "5" { "kind" = "environment" } { "name" = "URL" } { "value" = "https://example.test/path?a=b" } }
    { "6" { "kind" = "environment" } { "name" = "EMPTY" } { "value" = "" } }
    { "7" { "kind" = "environment" } { "name" = "EXPORTED" } { "value" = "factory" } }
    { "8" { "kind" = "environment" } { "name" = "QUOTED" } { "value" = "'single quoted'" } }

  test Pam_env.lns get environment = environment_tree

  test Pam_env.lns put environment after
    set "/1/value" "\"hello world\"" ;
    set "/4/value" "\"quoted phrase\"" ;
    set "/5/value" "x=y" ;
    set "/6/value" "" ;
    set "/8/value" "a,b"
  = "# service environment
SITE_MODE=\"hello world\"
GREETING=\"hello world\"
CSV_VALUES=one,two,three
EMPTY_VALUE=\"quoted phrase\"
URL=x=y
EMPTY=
EXPORTED=factory
QUOTED=a,b
"

(* Local Variables: *)
(* mode: caml *)
(* End: *)
