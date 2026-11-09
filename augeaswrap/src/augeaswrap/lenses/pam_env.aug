(*
Module: Pam_env
  Parses Linux-PAM's environment configuration files.

About: License
  This lens is original work for Augeaswrap and is licensed under the
  repository's MIT license. It does not copy an upstream Augeas lens.

About: Configuration files
  This lens accepts the two formats read by pam_env(8):

    * /etc/security/pam_env.conf records with DEFAULT= and OVERRIDE= fields
    * /etc/environment records with simple NAME=VALUE fields

  Quote delimiters are stored as part of the field value. This preserves the
  source spelling and lets callers explicitly quote whitespace-bearing values.
*)

module Pam_env =
  autoload xfm

  let eol = Util.doseol
  let empty = Util.empty_generic_dos /[ \t]*/
  let comment = Util.comment_generic_seteol /[ \t]*#[ \t]*/ "# " eol
  let name = [ label "name" . store /[A-Za-z0-9_]+/ ]
  let sep = del /[ \t]+/ " "

  (* pam_env(8) terminates an unquoted value at whitespace and a quoted value
     at the next double quote. Escaped double quotes are not supported. *)
  let conf_value = /"[^"\r\n]*"|[^ \t\r\n"=]+/
  let default =
    [ label "default" . Util.del_str "DEFAULT="
      . store conf_value ]
  let override =
    [ label "override" . Util.del_str "OVERRIDE="
      . store conf_value ]
  let conf_options =
    (sep . default . (sep . override)?) | (sep . override)
  let conf_fields =
    [ label "kind" . value "conf" ] . name . conf_options?

  (* The /etc/environment reader accepts single or double quote delimiters and
     treats # as the start of a comment. Commas are ordinary value characters. *)
  let env_value =
    [ label "value" . store /"[^"\r\n]*"|'[^'\r\n]*'|[^ \t\r\n#="]+/ ]
  let env_fields =
    [ label "kind" . value "environment" ]
    . name . del /=/ "=" . env_value

  let record =
    [ seq "entry" . Util.indent . (conf_fields | env_fields) . eol ]
  let lns = (empty | comment | record)*

  let filter =
    incl "/etc/security/pam_env.conf" .
    incl "/etc/environment" .
    Util.stdexcl

  let xfm = transform lns filter
