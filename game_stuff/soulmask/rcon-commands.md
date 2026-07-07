# commands available via RCON

queried using the interactive mode of our rcon wrapper

```bash
root@gstammtisch:/home/vb/volkb79-2/vbpub/scripts/gstammtisch-guide/scripts# ./exec-soulmask-rcon.sh -i
[rcon] container: 978056ad6306 (b87c0a5b-2387-4a1c-8863-ff23e6800a1d)
[rcon] connection test: List_OnlinePlayers
[rcon] connection OK
[rcon] interactive session: type commands at the prompt (e.g. 'help', then n/<page#>/q to page); Ctrl-D or 'quit' to exit
> help

   0: Disconnect
        Alia: q dc quit
        DESC: Close the manage conn.
        Example:

   1: ShowHelp
        Alia: help ?
        DESC: View Help.
        Example:

   2: SaveAndExit
        Alia: close exit shutdown
        DESC: After the number of seconds specified by the parameter, save and exit the game world.
        Example: [close 300] means that after the 300-second countdown, the server will be saved and closed.

   3: StopCloseServer
        Alia: cancelclose cc
        DESC: Cancel the previous server shutdown command, only valid before the server shutdown countdown ends.
        Example:

   4: SaveWorld
        Alia: sav
        DESC: Only save the world, do not exit the game. Parameter 1 means force save and write to hard disk immediately.
        Example: [save 1] means to force save the world.

   5: BackupDatabase
        Alia: bk backup
        DESC: Back up the game archive, the parameter is the name of the new archive.
        Example: [backup new_database_name] means to back up the archive in the same directory, named new_database_name.db

   6: BackupDatabaseByHour
        Alia: bkh
        DESC: Back up the archive in the same directory and name it YYYYmmddHH.db using UTC time..
        Example:

   7: Dump_AllActorPositions
        Alia: dap
        DESC: Export the positions of various Actors in the game to the file: Saved/ACTOR_POSI_DATA.log
        Example:

   8: DrawActorImage
        Alia: dai
        DESC: Draw the position of the specified type of Actor in the game to the image file: Saved/ACTOR_IMAGE_*.bmp
        Example: [dai 0] Draw the positions of all the characters in the game as images

   9: QueryInvitationCode
        Alia: qi
        DESC: Query game invitation code.
        Example:

  10: ServerFPS
        Alia: fps
        DESC: Query the average frame rate of the server for a short period of time.
        Example:

  11: ServerLoginStatus
        Alia: sl
        DESC: Continue/Pause player login to the game, parameter 0 means that new players will be prohibited from logging in to the game, 1 means that players can continue to log in.
        Example: [sl 0] will be prohibited from logging in to the game.

  12: QueryGridCount
        Alia: qg
        DESC:
        Example:

  13: DrawGrids
        Alia: dg
        DESC:
        Example:

  14: List_OnlinePlayers
        Alia: lp
        DESC: List Online Players (no args).
        Example: [lp]

  15: List_AllPlayers
        Alia: lap
        DESC: LList all Players (no args).
        Example: [lap]: List all players's base info

  16: List_SameBelongingObjs
        Alia: ls
        DESC: List the same belonging objects (with one arg: player's account or player controlled pawn's uid).
        Example: [ls player_account/character_uid]: list all chrarcter that have same belongings with the player.

  17: List_Guilds
        Alia: lg
        DESC: List guilds (no args).
        Example: [lg]: list all guild's name and uid in the world.

  18: List_GuildObjs
        Alia: lgo
        DESC: List guilds objects (with one arg: guild's name or guild's uid).
        Example: [lgo guild_name/guild_uid]: list the guild's characters(player,npc, ...)

  19: List_AllNPCClass
        Alia: lcc
        DESC: List all npc names and class names (with one string arg [optional], if not empty, results should contains the arg).
        Example: [lcc]: List all npc names and class names
        [lcc pig]: List all npc name and class name that name contains pig

  20: GotoPostion
        Alia: go
        DESC: Specify the player's uid/account number, and teleport the player to a location near coordinates (x,y,z).
        Example: [go character x y z]: Teleport character to (x, y, z)

  21: GotoTarget
        Alia: gonpc
        DESC: Specify a player's uid/account number, and teleport this player to the location of the specified player/NPC (specified by name, uid, account number).
        Example: [gonpc character1 character2]: Teleport character1 to character2

=========QUERY INTERACTIVE MODE========
|  PAGE:   1 of 3                     |
|  Enter any number to goto that page.|
|  Enter n to show next page.         |
|  Enter q to exit interactive mode.  |
=======================================
> n

  22: CreateSpecifiedMan
        Alia: cnpc
        DESC: Given the uid of the player account/control character, create a barbarian with the specified attributes CreateNo pre-configured number, Sex gender(0 represents male, 1 represents female)
        Example: [cnpc 76561192001000000 0 0]: Create a male NPC with preset number 0 for player '76561192001000000'

  23: CreateSWByClass
        Alia: create
        DESC: Specify the player account and class name to create mounts, NPCs, etc. belonging to the player
        Example: [create 76561192001000000 /Game/Blueprints/DongWu/BP_DongWu_Yu.BP_DongWu_Yu_C is_bady level nums quality_level]:
                Creates a fish/juvenile of a specified level and quality belonging to the player

  24: FlyMode
        Alia: fly
        DESC: Specify the player account, turn on or off the flight mode, (with an integer parameter: >0 means on, otherwise it means off)
        Example: [fly 76561192001000000 1]: Set the player's fly-mode on.

  25: Show_Coefficient_Settings
        Alia: lc
        DESC: List the game coefficient names containing the specified name and their current values.
        Example: [lc pvp] List configuration items containing 'pvp' (case insensitive)

  26: Set_Coefficient
        Alia: sc
        DESC: Set the game coefficient, with two parameters: the coefficient name of string type, and the coefficient value of floating point type.
        Example: [sc ExpRatio 5.0]: Set the multiplier of experience in-game items to 5 times

  27: Set_ServerPermissionEnable
        Alia: ssp
        DESC: Set the server permission list to be on or off, (
                0 Account allowed login list,
                1 Account blocked login list,
                2 IP allowed login list,
                3 IP blocked login list,
                4 Muted account list)
        Example: [ssp 1 0]: Disable the blocked account login list

  28: Set_ServerPermissionFlag
        Alia: sspf
        DESC: Set the server permission list flag (list type bitwise flag:
                1 account allowed login list,
                2 account blocked login list,
                4 IP allowed login list,
                8 IP blocked login list,
                16 banned list)
        Example: [sspf 26]: Set up account blocked login list, IP blocked login list and banned list (2+8+16=26) to enable, and other lists to disable

  29: Update_ServerPermissionList
        Alia: usp
        DESC: Add/remove certain items from the server permission list, with three parameters (integer type list type --- see Set_ServerPermissionEnable,
                the second parameter is greater than 0 to add, otherwise it means removal, the third parameter is a comma-separated string of accounts)
        Example: [usp 1 1 a,b,c]: Indicates adding three accounts to the blocked login list, a, b and c

  30: List_ServerPermissionList
        Alia: lsp
        DESC: View the server permissions list (no args).
        Example: [lsp]

  31: Set_OutputChats
        Alia: soc
        DESC: Output the contents of world chat, nearby chat and guild chat to the LOG file
        Example: [soc 1]: means turning on this feature

  32: Update_RconClientAddress
        Alia: ura
        DESC: Add/delete RCON client address whitelist, multiple IP addresses are separated by commas
        Example: [ura 1 127.0.0.1,192.168.1.2]: Add 127.0.0.1 and 192.168.102 to RCON's IP whitelist

  33: List_AllItemClass
        Alia: lai
        DESC: List all prop names and class information. There is a string parameter (optional) to filter out prop information whose names contain this string.
        Example: [lai stone]: List all prop information containing stone in the default prop name

  34: CreateItemForPlayer
        Alia: citem
        DESC: Create an item for a specific player. There are 4 parameters, in order: player account, item type, quantity and quality.
        Example: [citem 76561192001000000 /Game/Blueprints/DongWu/BP_DongWu_Yu.BP_DongWu_Yu_C 1 5]: Create 1 red quality item

=========QUERY INTERACTIVE MODE========
|  PAGE:   2 of 3                     |
|  Enter any number to goto that page.|
|  Enter n to show next page.         |
|  Enter q to exit interactive mode.  |
=======================================
> n

  35: List_AllTalent
        Alia: lat
        DESC: Displays the talent entry information of the corresponding level, accepts an integer parameter (talent level, less than 0 means displaying all talents).
        Example: [lat 3]: List all of level 3 talents.

  36: SayToSystemChannel
        Alia: say
        DESC: send system message to all online players
        Example: [say helloworld]

  37: DeleteItem
        Alia: del
        DESC: delete player's item. There are 3 parameters, in order: player account, item class path, and Expected number to be deleted.
        Example: [del 76561192001000000 /Game/Blueprints/DongWu/BP_DongWu_Yu.BP_DongWu_Yu_C]: Will delete all the specified items in this player's backpack.

  38: ExecScriptCommands
        Alia: run
        DESC: parse and run a script file (full_command / line).
        Example: [run a.txt]: Will run all commands in file: $(Game Saved path)/a.txt

  39: ClearAllNpc
        Alia: can
        DESC: Delete all NPCs on the server that do not belong to the player. (no parameters)
        Example: [can]: will delete all NPCs on the server that do not belong to the player.

=========QUERY INTERACTIVE MODE========
|  PAGE:   3 of 3                     |
|  Enter any number to goto that page.|
|  Enter n to show next page.         |
|  Enter q to exit interactive mode.  |
=======================================
```

