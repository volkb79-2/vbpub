# Dialogue System Documentation

## 1. UI Textflow

* **Text forwarding (player convenience)**: Right Mouse Button (RMB) skips the roll-in text effect and shows the page at once.

## 2. Global Properties & Basics

These properties are defined directly in the dialogue state.

* **`NPCName`**: Overrides the default name of the entity/block in the dialogue header.
    * **Note:** Must be set in the **FIRST** element of the dialogue!
* **`Output`**: The text displayed by the NPC/Questgiver to the player.
    * **PlayerName Variable**: Use `Welcome {PlayerName}` in the Output to greet the player with their profile name. This also works in `Option_` texts.
* **`SetNPCName('name')`**: Can be used as a function to change the `NPCName` in subsequent states of the same dialogue.

---

## 3. Dialogue Flow (Basics)

There are two main methods to advance a dialogue: player choices (`Option*`) or automatic/logical checks (`Next*`).

### 3.1. Basics: By Player Choice (Option\*)

Used when the player should make a choice.

1.  **`Option_1`**: The text the player can select.
2.  **`OptionNext_1`**: Defines the next dialogue state that follows the selection.
3.  **`OptionIf_1`**: (Optional) The dialogue only proceeds to `OptionNext_1` if this condition is met.
4.  **`OptionExecute_1`**: (Optional) A function that is called when `Option_1` is chosen AND/OR `OptionIf_1` is met.

* **Note**: You can add multiple combos (e.g., `Option_2`, `OptionNext_2`...). Ensure the numbers match.
* **Values for `OptionNext_`**:
    * `End`: Closes the dialogue.
    * `'OtherDialogueName'`: Jumps to the specified dialogue state.

### 3.2. Basics: By Logic / Automatically (Next\*)

Used for automatic dialogue sequences based on conditions.

1.  **`NextIf_1`**: The condition that is checked (e.g., `HasItem('Token', 1)`).
2.  **`Next_1`**: The target dialogue state if the condition (`NextIf_1`) is met.
3.  **`Execute_1`**: (Optional) A function that is called when `NextIf_1` is met.

* **Note**: You can also define multiple pairs here (`NextIf_2`, `Next_2`...).
* **Values for `Next_`**:
    * `End`: Closes the dialogue.
    * `'OtherDialogueName'`: Jumps to the specified dialogue state.

---

## 4. Special Dialogue Properties

### 4.1. Barking State

* If a dialogue supports a barking state, its `Output` text will be displayed in a UI popup as soon as the player focuses (looks at) the NPC entity or block.
* **Implementation**: Add the tag `BarkingState: <state_name>` in the start dialogue state.
* **Example**: `BarkingState: TC_SaySomethingInteresting`
* **Note**: The barking state ignores all `Option_` or `Next_` entries and only displays the `Output` text.

### 4.2. Globals (Constants)

* Globals or constants can be defined in an empty dialogue state.
* They are readable in ALL dialogue states afterward without needing to be declared as a variable.
* Ideal if you need more than 30 variables.
* **IMPORTANT**: They cannot be overwritten and are **read-only**!
* **Example**: `int LotteryNumber = 7;`

### 4.3. Dynamic Jump Target

* Allows defining a jump target via a string variable, useful for sub-routines.
* **Usage**: Use `@variablename` as the target in `OptionNext_1` or `Next_1`.
* **Definition**: `Execute_1: "variablename = 'TargetStateName'"`
* **`RequiredStates` (IMPORTANT!)**:
    * Because the variable cannot be read at compile time, the target state (`TargetStateName`) might not be compiled.
    * Add `RequiredStates: "state1, state2, state3"` in the start state to force the compilation of these states.
* **Other Jump Commands**:
    * `Return`: Use `param1` to set a return target.
    * `GotoAndReset`

### 4.4. User Input

Allows displaying an input dialogue for `int`, `float`, or `string`.

* **Syntax**: `Input: "<text>", param1: <type>;<ok button>;<cancel button>`
    * `<type>` must be `int`, `float`, or `string`.
    * Strings are limited to 30 characters.
* **Note**: See **Section 7.0 Helper Functions** for functions used with User Input (`IsInputOk()`, `IsInputCancel()`, `InputResultAs...()`).

**Example for User Input:**
```

{ +Dialogue Name: Test\_Input\_A\_Number
Variable\_1: "result", param1: int
Input: "Please enter an integer:", param1: int;Yes;No
Execute: "if (IsInputOk()) result = InputResultAsInt();"
Next\_1: State\_YesPressed
NextIf\_1: "IsInputOk()"
Next\_2: State\_NoPressed
NextIf\_2: "IsInputCancel()"
}

```

### 4.5. Functions (C# Code)

Allows writing C# logic for complex tasks.

* **Hint**: Use `UnityEngine.Debug.Log()` to see output in the in-game console.
* **CDATA Notice**: For native C# code (especially in `Execute_`), use `<CDATA> ... </CDATA>` to use double quotes (`"`) without issues.

### 4.6. Hybrid Functions (BlocksConfig.ecf)

Special parameters for Blocks and Devices set in `BlocksConfig.ecf`:

* **`ExecuteOnActivate: YourDialogState`**
    * Adding a dialogue state from the `dialogues.ecf` will allow the block/device to activate that dialogue by facing the block and clicking F.
    * *Note*: Does not require 'IsActivatable' or other properties.
    * *Note*: If device already has 'IsActivatable' or other activatable-state/Access (F-button, like containers, levers etc) their Access-function will be executed primarily BUT the dialogue will be run (invisible) never the less!
* **`ExecuteOnCollide: YourDialogState`**
    * Adding a dialogue state from the `dialogues.ecf` will allow the block/device to activate that dialogue by touching/pressing against that block. (similar to `ExecuteOnActivate`!)
* **`DialogueSingleUserAccess: true`**
    * Only one player at a time can access the dialogue triggered by the device/blocks.
* **`DialogueState: YourDialogState`**
    * Allows specifying a hard-coded target dialog. If this is present, this block can be placed by users on PLAYER owned bases and be also triggered using "F".
    * *ATTENTION!* Setting a dialogue state here will overwrite manually set Dialogues for this device in the blueprints!
* **`OmitCone: true`**
    * *Default: false*. If `true`, hides the yellow dialogue cone (f.ex if the NPC only has a barking dialogue).

### 4.7. Text Formatting & Flow Management

* **Formatting**: Colors, bold (`<b>`), and italic (`<i>`) can be used in the `Output` text.
* **Text Flow (Pagination)**: Controls the timing and page breaks of the NPC text.
* **Global Format**: `@<type><time>`
    * **`<p>` (Page)**: Breaks the text into pages with automatic forwarding.
        * `@p9`: **Stops** automatic forwarding; waits for player input.
        * `@p0`: **Forces** the player to stay in the dialogue (cannot close with ESC/X).
    * **`<w>` (Wait)**: Pauses the text flow after a word for the specified time.
    * **`<d>` (Delay/Speed)**: Sets the text speed (words per second) for the current page. `1` (slow) to `9` (fast).
        * `@d0`: Shows the remaining text of the page instantly.
    * **`<time>` (Time)**: A digit (0-9). Each digit equals 0.5 seconds. (e.g., `@w2` = 1 second wait).
* **Example**: `"It seems...@w2 you have no Gold...@p3Get some and come back. Bye"`
* **Escape Character**: To use an `@` literally, use `@@` (e.g., `@@w2Player`).

---

## 5. Variables

### 5.1. Local Variables (Standard)
* `int`
* `bool`
* `string` (Must be set in single quotes `''`)
* `float`

### 5.2. Database Variables (Persistent)
* **`dbstate_int`**: Applies to this player, but only for this start state.
* **`dbplayer_int`**: Applies to this player in *all* dialogues.
* **`dbplayerpf_int`**: Applies to this player in all dialogues, but only on the current playfield.
* **`dbplayerpoi_int`**: Applies per player only within the current POI.
* **`dbplayerpoipos_int`**: Like `dbplayerpoi_int`, but preserves its value even after the POI regenerates (e.g., for traders).
* **`dbglobal_int`**: Applies globally to all players and all dialogues.
* **`dbglobalpf_int`**: Applies globally to all players, but only within the current playfield.
* **`dbglobalpoi_int`**: Applies globally to all players within the current POI.
* **String Versions** (Max 125 characters):
    * `dbstate_string`
    * `dbplayer_string`
    * `dbglobal_string`
    * `dbglobalpoi_string`

### 5.3. Read-Only Global Variables
* **`PlayerName`**: Name of the local player.
* **`PlayersInPlayfield`**: Number of players on the current playfield.

---

## 6. Player Properties (Player States)

These can be read directly and often modified in `NextIf_` or `Execute_`.

* `Player.Stamina = 100`
* `Player.Health -= 1`
* `Player.Oxygen = 30`
* `Player.Food += 1`
* `Player.Credit += 1.1` (Credits on the bank account, NOT the moneycard!)
* `Player.Origin == 0` (Checks the origin; requires the ID from `sectors.yaml`)
* `Player.ExperiencePoints += 1` (Sets/gets player XP)
* `Player.Level` (Reads the player's level, cannot be set directly)
* `Player.UpgradePoints += 1` (Sets/gets player Upgrade/UnlockPoints)
* `Player.Name` (Reads player name, `string`, readonly)
* `Player.Id` (Reads Steam ID, `string`, readonly)
* `Player.EntityId` (Reads in-game entity ID, `int`, readonly)
* `Player.PlayfieldName` (Reads the name of the current playfield)
* **`Player.Skills['skillname']` (float)**
    * Sets or gets player-local skill values via the `dialogues.ecf`.
    * **Example**: `Player.Skills['Rhetoric'] = 5.0`
    * **Modifiers**: The skill values can be used to modify properties of blocks and items: `Damage`, `BlastDamage`, `BulletSpread`, `ReloadDelay`, `Recoil`, `RangeAU` and `RangeLY`.
    * **Config Example**: `Mod.ReloadDelay: "ReloadDelay + Player.Skill['skillname']"`
    * **Note**: Use `Player.Skills['skillname'] += 10` to add points.
    * **Note**: To use the value in the dialogue, it may need to be copied to a local variable.

---

## 7. Advanced Functions & API Calls

List of functions for `Execute_`, `OptionExecute_`, or `NextIf_`.

### 7.0 Helper Functions
* **`IsInputOk()` (bool)**: Used in the Input property. Returns `true` if the player pressed the Ok button.
* **`IsInputCancel()` (bool)**: Used in the Input property. Returns `true` if the player pressed the Cancel button.
* **`InputResultAsInt()`, `InputResultAsFloat()`, `InputResultAsString()`**: Used in the Input property. Returns the player input already converted into the correct type.
* **`Random(x,y)`**: Shuffles a number from x up to number y.

### 7.1. C# Core API
* **`GetItems()`**: Returns a list of all player items.
    * **Example**: `foreach (DialogueSystem.ItemInfo itemInfo in GetItems()) { if (itemInfo.Name == 'Token') return true; }`
    * **Struct**: `public struct ItemInfo { public string Name; public int Id; public int Count; public int Meta; }`
* **`CallLater(TimeInSeconds, FunctionName)`**: Executes the C# function `FunctionName` after `TimeInSeconds`. **Do NOT over-use it!!**
* **`LocalStructure?`**: Returns `IStructure` data of the structure the player is currently in.
* **`OpenDevice`**: Accesses the device whose window is currently open. (e.g., `IContainer container = OpenDevice;`)
* **`OpenDevicePos`**: Returns the block position of the currently open device. (e.g., `VectorInt3 blockPos = OpenDevicePos;`)
* **`GetStructure(id)`**: Reference to a structure via its ID (get ID via `LocalStructure.Entity.Id`, for example).
* **`GetFaction(id)`**: Returns the faction name of the given structure ID.

### 7.2. Reputation & Items
* **`GetReputation(Faction.Talon) < Reputation.FriendlyMin`**: Reputation check.
* **`GetReputation(Faction.Talon) < 24000`**: Reputation check with numeric values. (Tiers: 0-6k Hostile, 6k-12k Unfriendly, 12k-18k Neutral, 18k-24k Friendly, 24k+ Honored)
* **`AddReputation(Faction.Zirax, -100)`**: Adds/removes reputation points.
* **`HasItem('KeyCardBlack', 3, 1234)`**: Checks for item (Name, Count, Meta (optional)).
* **`AddItem('GoldCoins', 2*GoldCoinsPlayerBet)`**: Adds item (Name, Count, Meta (optional)).
* **`RemoveItem('KeyCardBlack', 3, 1234)`**: Removes item (Name, Count, Meta (optional)).
* **`AddItemsFromContainer(int containerId, int maxItemCount)`**: Gives the player items based on a definition in `Containers.ecf`.

### 7.3. Blocks & Signals
* **`SetBlockActive('BlockName', true/false)`**: Toggles a block on/off by name.
* **`IsBlockActive('BlockName')` (bool)**: Checks if a block is active.
* **`SetSignal('SignalName', true/false)`**: Sets a logic signal.
* **`IsSignalSet('SignalName')` (bool)**: Checks a logic signal.
* **`ReplaceBlocks(int entityId, string source, string target, ...)`**: Replaces blocks in a structure, similar to the `replaceblocks` command.
    * **Signature**: `ReplaceBlocks(int entityId, string sourceBlockName, string targetBlockName, string parentIndex = null, bool bSourceBlockNameIsUserDefined = false, bool bStripBlockAttributes = false)`
    * **Example**: `ReplaceBlocks(1077, 'My Constructor', 'ConstructorT2', bSourceBlockNameIsUserDefined: true)`
    * `bSourceBlockNameIsUserDefined = true`: Allows using the name from the Control Panel (e.g., 'My Constructor').
    * `bStripBlockAttributes = false`: If `true`, texture/color properties are stripped.

### 7.4. TechTree
* **`UnlockTechTreeItem('string blockOrItemName')` (bool)**: Unlocks an item/block in the techtree.
    * Returns `true`/`false`. If the cost is set to -1, it can *only* be unlocked with this function.
* **`IsTechTreeItemUnlocked('string blockOrItemName')` (bool)**: Checks if an item/block is unlocked.

### 7.5. PDA, Bookmarks & Time
* **`IsPdaChapterActive('ChapterTitleYamlKey')` (bool)**: Checks PDA chapter (uses the key from `PDA.yaml`, not the localized text).
* **`IsPdaTaskActive('ChapterTitleYamlKey', 'TaskTitleYamlKey')` (bool)**: Checks PDA task.
* **`AddBookmark(...)`**:
    * **Version 1 (Simple)**: `AddBookmark('Test Name', 2, 'Jite Pi', 4000, 5000, 6000)` (name, type, playfield name, posx, posy, posz).
    * **Version 2 (Complex, with Delegate)**: `AddBookmark('test name', 1, 'Temperate Planet', 1, 2, 3, delegate(int bmId){ Debug.Log('BmId=' + bmId); })`
    * `Type`: 0=Solar System, 1=Galaxy Map, 2=Space, 3=Planet.
* **`RemoveBookmark(int bmId)`**: Removes a bookmark via its ID (obtained from the `AddBookmark` delegate).
* **`GetGameTimeInSec()` (int)**: Returns the game time in seconds since the game started.
* **`DeltaTimeToString(gametime)` (string)**: Formats a time interval (e.g., "1.2 days" or "0.7 h").

### 7.6. Global Database Variables (Modifying)
* **`AddToGlobalVar('VarName', increment)` (int)**: Only use this function for global DB vars (+/- increments) to avoid multi-user conflicts.
* **`AddToGlobalPfVar('VarName', increment)` (int)**: (See above, for Playfield vars)
* **`AddToGlobalPoiVar('VarName', increment)` (int)**: (See above, for POI vars)
* **`SetGlobalVar('VarName', val)` (int/string)**: Sets a global variable directly. **Attention**: Can be overwritten by other players!
* **`SetGlobalPfVar('VarName', val)` (int)**: (See above, for Playfield vars)
* **`SetGlobalPoiVar('VarName', val)` (int/string)**: (See above, for POI vars)

### 7.7. Windows & UI
* **`OpenTraderWindow()`**: Opens the trader window (only works if the dialogue was started from a trader block!).
* **`OpenHtmlWindow(...)` (void)**: Opens an HTML window (in-game browser).
    * **Signature 1**: `OpenHtmlWindow(string title, string url, float widthPerc, float heightPerc)`
    * **Signature 2 (Full)**: `OpenHtmlWindow(string title, string url, float horizontalePerc, float verticalPerc, float widthPerc, float heightPerc, bool _bShowNavButtons, _bgColor: null, _bBlockBG: false)`
    * **YouTube Ex**: `OpenHtmlWindow('Trailer', 'https://.../embed/xyz?rel=0&autoplay=1', 0.26f, 0.34f, 0.48f, 0.21f, _bShowNavButtons:false, _bgColor: new Color(1.0f,0.0f,0.0f,1.0f))`
    * **YouTube Parameters**:
        * `rel=0`: Prevents recommended videos from other channels.
        * `autoplay=1`: Does not work (yet), player must click.
        * `controls=0`: Hides the YouTube controls.
    * **Local Files**:
        * `%SHARED_DATA%`: `root/Content/Scenarios/YOUR_Scenario/`
        * `%CONTENT_DIR%`: `root/Content/`
    * **Window Parameters**:
        * `widthPerc`, `heightPerc`: Window size (e.g., `0.75f`).
        * `horizontalePerc`, `verticalPerc`: Window position.
        * `_bShowNavButtons`: (true/false) Show navigation buttons.
        * `_bgColor`: Background color (4th value is alpha/transparency).
        * `_bBlockBG`: (true/false) Blocks/unblocks the dialogue buttons behind it.
* **`CloseHtmlWindow()` (void)**: Closes the currently open HTML window.

**Examples**
OpenHtmlWindow('','%SHARED_DATA%/Content/SpaceTest7Antwort.jpg',0.1885f,0.211f,0.6235f,0.40f,_bShowNavButtons:false,_bBlockBG:false,_bgColor: new Color(0.05f,0.09f,0.11f,0.9f))
OpenHtmlWindow('','%CONTENT_DIR%/Extras/Assets/SpaceTest7Antwort.jpg',0.1885f,0.211f,0.6235f,0.40f,_bShowNavButtons:false,_bBlockBG:false,_bgColor: new Color(0.05f,0.09f,0.11f,0.9f))

### 7.8. Miscellaneous Functions
* **`GetInstanceTicket()` (int)**: Returns the index number of the current instance (or 0).
* **`GetInstanceTicket('playfieldname')` (int)**: Returns the index number of a specific playfield's instance.
* **`Loc(string key)` (string)**: Dynamically fetches a localized text from the Loca CSV.
* **`LocF(string key, var1, ...)` (string)**: Like `Loc`, but supplies variables (as `{0}`, `{1}` in the CSV).
* **`IsFactionDiscovered(Faction.Name)` (bool)**: Checks if the faction has been discovered by the player.

### 7.9. Vessel & Jumpgate Functions

#### Vessel Type & Size Functions

* **`GetVesselType()` (string)**: Returns the type of vessel the player is currently in.
    * **Returns**: `"CV"`, `"SV"`, `"HV"`, `"BA"`, `"OnFoot"` (if not in a vessel), or `"Unknown"` (on error).
    * **Use Case**: Restrict access based on vessel type (e.g., only CVs can use interstellar jumpgates).

* **`GetVesselSize(string axis)` (int)**: Returns a specific dimension of the player's current vessel in meters.
    * **Parameter**: `axis` - The axis to measure: `"X"`, `"Y"`, or `"Z"` (case-insensitive).
    * **Returns**: The dimension in meters (rounded up), or `0` if player is on foot or not in a vessel.
    * **Note**: Values match the STATS screen. Conversion: CV/BA = 2m per block, SV/HV = 0.5m per block.
    * **Example**: `GetVesselSize('X')` returns the X dimension of the vessel.

* **`GetVesselCrossSection()` (int)**: Returns the cross-section size of the player's current vessel.
    * **Returns**: The SECOND-LARGEST of X, Y, Z dimensions in meters (rounded up).
    * **Purpose**: Useful for "2 out of 3" size checks (e.g., can a long/thin ship fit through a portal?).
    * **Example**: A 200m x 30m x 30m ship returns `30` (the second-largest dimension).

**Example: Vessel Type Check**
```
{ +Dialogue Name: VesselTypeCheck_Start
Variable_1: "vesselType", param1: string
Execute_1: "vesselType = GetVesselType()"
Next_1: VesselTypeCheck_OnFoot
NextIf_1: "vesselType == 'OnFoot'"
Next_2: VesselTypeCheck_SmallVessel
NextIf_2: "vesselType == 'SV' || vesselType == 'HV'"
Next_3: VesselTypeCheck_LargeVessel
NextIf_3: "vesselType == 'CV' || vesselType == 'BA'"
Next_4: VesselTypeCheck_Unknown
}

{ +Dialogue Name: VesselTypeCheck_OnFoot
Output: "You must be in a vessel to use this jumpgate."
}

{ +Dialogue Name: VesselTypeCheck_SmallVessel
Output: "Your {vesselType} is too small for interstellar travel. You need a Capital Vessel (CV)."
}

{ +Dialogue Name: VesselTypeCheck_LargeVessel
Output: "Your {vesselType} is approved for jumpgate travel."
Next_1: Jumpgate_Proceed
}

{ +Dialogue Name: VesselTypeCheck_Unknown
Output: "Unable to determine vessel type. Please try again."
}
```

**Example: Custom Vessel Size Check**
```
{ +Dialogue Name: VesselSizeCheck_Start
Variable_1: "sizeX", param1: int
Variable_2: "sizeY", param1: int
Variable_3: "sizeZ", param1: int
Variable_4: "maxSize", param1: int
Execute_1: "sizeX = GetVesselSize('X')"
Execute_2: "sizeY = GetVesselSize('Y')"
Execute_3: "sizeZ = GetVesselSize('Z')"
Execute_4: "maxSize = Math.Max(Math.Max(sizeX, sizeY), sizeZ)"
Next_1: VesselTooLarge
NextIf_1: "maxSize > 100"
Next_2: VesselSizeOK
}

{ +Dialogue Name: VesselTooLarge
Output: "Your vessel's largest dimension is {maxSize}m. Maximum allowed is 100m!"
}

{ +Dialogue Name: VesselSizeOK
Output: "Your vessel dimensions: {sizeX}m x {sizeY}m x {sizeZ}m. You may proceed."
}
```

**Example: Cross-Section Check (Jumpgate-style)**
```
{ +Dialogue Name: CrossSectionCheck_Start
Variable_1: "crossSection", param1: int
Variable_2: "portalSize", param1: int
Execute_1: "crossSection = GetVesselCrossSection()"
Execute_2: "portalSize = 174"
Next_1: VesselTooWide
NextIf_1: "crossSection > portalSize"
Next_2: VesselCanPass
}

{ +Dialogue Name: VesselTooWide
Output: "Your vessel's cross-section ({crossSection}m) exceeds the portal size ({portalSize}m)!"
}

{ +Dialogue Name: VesselCanPass
Output: "Cross-section check passed ({crossSection}m fits through {portalSize}m portal)."
}
```

#### Jumpgate Teleportation

* **`TeleportThroughJumpgate(string destinationString)` (void)**: Teleports the player and their vessel through a jumpgate to a destination.
    * **Parameter**: `destinationString` - Destination in format: `"DeviceName@StructureName:PlayfieldName@SolarSystemName"`
      * `DeviceName`: Name of the destination jumpgate device (block name in Control Panel)
      * `StructureName`: Name of the structure containing the destination jumpgate
      * `PlayfieldName`: Name of the destination playfield
      * `SolarSystemName`: Name of the destination solar system
    * **Returns**: Nothing (void). The teleport is initiated asynchronously.
    * **Note**: The dialogue should ALWAYS close immediately after calling this function.
    * **Requirements**: Player must be in a vessel. 

**Example: Simple Jumpgate Teleport**
```
{ +Dialogue Name: Jumpgate_Confirm
Output: "Initiating jump sequence to Crown Sector..."
Execute_1: "TeleportThroughJumpgate('Jumpgate@CROWN:Crown Sector@Ellyon')"
Next_1: End
}
```

**Example: Full Jumpgate Dialogue with Checks**
```
{ +Dialogue Name: Jumpgate_Start
NPCName: "Jumpgate Control"
Variable_1: "vesselType", param1: string
Variable_2: "crossSection", param1: int
Variable_3: "maxSize", param1: int
Execute_1: "vesselType = GetVesselType()"
Execute_2: "crossSection = GetVesselCrossSection()"
Execute_3: "maxSize = 174"
Next_1: Jumpgate_NoVessel
NextIf_1: "vesselType == 'OnFoot'"
Next_2: Jumpgate_WrongType
NextIf_2: "vesselType != 'CV'"
Next_3: Jumpgate_TooLarge
NextIf_3: "crossSection > maxSize"
Next_4: Jumpgate_Ready
}

{ +Dialogue Name: Jumpgate_NoVessel
Output: "Error: No vessel detected. You must be aboard a ship to use this jumpgate."
}

{ +Dialogue Name: Jumpgate_WrongType
Output: "Error: Only Capital Vessels (CV) can use interstellar jumpgates. Your {vesselType} is not compatible."
}

{ +Dialogue Name: Jumpgate_TooLarge
Output: "Error: Your vessel's cross-section ({crossSection}m) exceeds the jumpgate aperture ({maxSize}m)."
}

{ +Dialogue Name: Jumpgate_Ready
Output: "All systems nominal. Your CV is cleared for jump."
Option_1: "Initiate jump to Crown Sector"
OptionNext_1: Jumpgate_Jump_Crown
Option_2: "Initiate jump to Tallodar System"
OptionNext_2: Jumpgate_Jump_Tallodar
Option_3: "Cancel"
OptionNext_3: End
}

{ +Dialogue Name: Jumpgate_Jump_Crown
Output: "Engaging hyperdrive... Destination: Crown Sector, Ellyon System."
Execute_1: "TeleportThroughJumpgate('Jumpgate@CROWN:Crown Sector@Ellyon')"
Next_1: End
}

{ +Dialogue Name: Jumpgate_Jump_Tallodar
Output: "Engaging hyperdrive... Destination: Tallodar Prime, Tallodar System."
Execute_1: "TeleportThroughJumpgate('Jumpgate@TallodarGate:Tallodar Prime@Tallodar')"
Next_1: End
}
```

---

## 8. Examples

### 8.1. Handling Tips
* When chaining randomizers, ensure different values are used.

### 8.2. Example: Trader Dialogue Switch
* Adds a dialogue option before opening the trade window.
* **Usage**: Add `Trader_DialogueSwitch_Start` to the Dialogue field of the Trader in the Control Panel.

```

{ +Dialogue Name: Trader\_DialogueSwitch\_Start
NPCName: "Trader"
Output: "How may I serve you?"
Option\_1: What are the latest news?
OptionNext\_1: Trader\_DialogueSwitch\_Talk
Option\_2: I want to trade
OptionNext\_2: Trader\_DialogueSwitch\_Trade
}
{ +Dialogue Name: Trader\_DialogueSwitch\_Talk
NPCName: "Trader"
Output: "The quantumelectric field in Gamma Orionis has shown some fluctuations."
Option\_1: Ah..interesting
OptionNext\_1: Trader\_DialogueSwitch\_Start
Option\_2: Bye
OptionNext\_2: End
}
{ +Dialogue Name: Trader\_DialogueSwitch\_Trade
NPCName: "Trader"
Execute\_1: OpenTraderWindow()
}

```

### 8.3. Example: Talon Chief (Variables & Conditions)

```

{ +Dialogue Name: TC\_Start
NPCName: Talon Chief
Variable\_1: "FoodCounter",   param1: int
Variable\_2: "TalkCount",     param1: dbstate\_int
Variable\_3: "PlayerTalkExp", param1: dbplayer\_int
Variable\_4: "GlobalVar",     param1: dbglobal\_int
Execute\_1: "TalkCount = TalkCount + 1"
Execute\_2: "PlayerTalkExp = PlayerTalkExp + 1"
Execute\_3: "GlobalVar += AddToGlobalVar('GlobalVar', 3)"
Next\_1: TC\_RepuBad
NextIf\_1: "GetReputation(Faction.Talon) \< Reputation.NeutralMin"
Next\_2: TC\_HasHolyStatus
NextIf\_2: "HasItem('KeyCardBlack',3)"
Next\_3: TC\_TalkingOk
NextIf\_3: "TalkCount \> 3 && Random(1, 4) == 1"
Next\_4: TC\_CameBack
NextIf\_4: "TalkCount \> 1"
Next\_5: TC\_DefaultEntry
}
{ +Dialogue Name: TC\_CameBack
Output: "Hey we talked already {TalkCount} times, that's enough for now\!"
}
{ +Dialogue Name: TC\_TalkingOk
Output: "Let's start from the beginning"
Execute\_1: "TalkCount = 1"
Next\_1: TC\_DefaultEntry
}
{ +Dialogue Name: TC\_RepuBad
Output: "dlgTCBadRepu"
}
{ +Dialogue Name: TC\_HasHolyStatus
Output: "dlgTCHolyStatue"
Option\_1: "Yes, I give it to you as a present"
OptionNext\_1: TC\_GiveHolyStatue
Option\_2: "No, I want to keep it"
OptionNext\_2: TC\_DefaultEntryCont
Option\_3: "I need to go"
OptionNext\_3: End
}
{ +Dialogue Name: TC\_DefaultEntry
Output: "dlgTCGreetings"
Next\_1: TC\_DefaultEntryCont
}
{ +Dialogue Name: TC\_DefaultEntryCont
Option\_1: "dlgTCTellMeStory"
OptionNext\_1: TC\_AskStoryAboutPeople
Option\_2: "Can you give me some food?"
OptionNext\_2: TC\_CheckFood
Option\_3: "I need to go."
OptionNext\_3: End
}
{ +Dialogue Name: TC\_GiveHolyStatue
Output: "I never dreamt that I will get back the statue\! I will give you rich presents for that."
RemoveItem: ""
AddItem: ""
Next\_1: TC\_AskForMore
}
{ +Dialogue Name: TC\_CheckFood
Next\_1: TC\_TooMuchFood
NextIf\_1: "FoodCounter == 1"
Next\_2: TC\_TooMuchFood2
NextIf\_2: "FoodCounter \> 1"
Next\_3: TC\_GiveFood
}
{ +Dialogue Name: TC\_GiveFood
Execute\_1: "FoodCounter = FoodCounter + 1"
Output: "Yes of course. Here is some sea grass. Can I help you more?"
Next\_1: TC\_DefaultEntryCont
}
{ +Dialogue Name: TC\_TooMuchFood
Execute\_1: "FoodCounter = FoodCounter + 1"
Output: "You already got food but here is some more sea grass. Now choose something else\!"
Next\_1: TC\_DefaultEntryCont
}
{ +Dialogue Name: TC\_TooMuchFood2
Output: "You will not get any more food\! Bye\!"
}

```

### 8.4. Example: BlackJack (Complex Logic & Functions)
*(Note: Shortened for brevity, full logic included)*
```

{ +Dialogue Name: BJ\_Start
NPCName: txt\_UGmuO
Variable\_1: "PlayerCards", param1: int
Variable\_2: "TalkCount", param1: dbstate\_int
...
Output: txt\_mWeCe
Next\_1: BJ\_NoMoney
NextIf\_1: "\!HasItem('GoldCoins',1)"
Next\_2: BJ\_AskBet
NextIf\_2: "HasItem('GoldCoins',1) && TotalGamesLost \< 20 && TotalGamesWon \< 10"
Next\_3: BJ\_Cooldown\_Lost
NextIf\_3: "TotalGamesLost \>= 20"
Next\_4: BJ\_Cooldown\_Won
NextIf\_4: "TotalGamesWon \>= 10"
}
{ +Dialogue Name: BJ\_AskBet
Variable\_1: "GoldCoinsPlayerBet", param1: int
Output: txt\_mq8OK
Option\_1: txt\_4WUCq
OptionNext\_1: BJ\_RemoveCoins
OptionExecute\_1: "GoldCoinsPlayerBet = 1"
...
}
{ +Dialogue Name: BJ\_AskCard
Output: txt\_aqCI0
Option\_1: txt\_GiiaK
OptionNext\_1: BJ\_NewCard
Option\_2: txt\_8OGas
OptionNext\_2: BJ\_DealerTakesFirstCard
}
{ +Dialogue Name: BJ\_DealerTakesAnotherCard
Variable\_1: "DealerNewCard", param1: int
Execute\_1: "DealerNewCard = Random(1,11)"
Execute\_2: "DealerCards = DealerCards + DealerNewCard"
Output: txt\_0CqKm
Next\_1: BJ\_DealerTakesAnotherCard
NextIf\_1: "DealerCards \< 21 && DealerCards \< PlayerCards"
Next\_2: BJ\_PlayerWins
NextIf\_2: "PlayerCards \> DealerCards || DealerCards \> 21"
Next\_3: BJ\_DealerWins
}
{ +Dialogue Name: BJ\_PlayerWins
Output: txt\_40jMG
Execute\_1: "AddItem('GoldCoins', 2\*GoldCoinsPlayerBet)"
...
Next\_1: BJ\_AskForNewGame
}
{ +Dialogue Name: BJ\_Bye
Output: txt\_O8CaF
}

```
```