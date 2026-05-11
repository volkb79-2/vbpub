# Bug List to be resolved



## 1

Issue: added spacing between number and percentage after MT. MT performs incorrect translation adding space.


example reference: `### 13288. Localization.csv:2046:itmLaserPistolT1`
**en_sent_to_mt_normalized**: `(TKPH0RTK) +20% Damage vs Mechanical (TKPH1LTK)`
**de_returned_by_mt_raw**: `(TKPH0RTK) +20 % Schaden gegenüber Mechanik (TKPH1LTK)`
**de_final_game_ready**: `\n[c][00FF00]+20 % Schaden gegenüber Mechanik[-][/c]`



## 2 

Issue: 'CV' gets translated to 'Lebenslauf' 

example: `Locailzation.csv`: `MS,[c][ffe74d]CV[-][/c],[c][ffe74d]Lebenslauf[-][/c],,,,,,,,,,,,,,,`

## 3 

Issue: created `de_final_game_ready` did not make it into final csv, only english version is present, 

example reference `dialogue_4Wa40`, `dialogue_q84WO`, 



## 4 

reference: `### 632. Dialogues.csv:633:dialogue_4Wa40`, `Dialogues.csv:699:dialogue_WaeW8`
Issue: some `@w3` printed as is in game, not interpreted as control sequence.

Idea: in the generated `de_final_game_ready`,  missing space after the control sequence, such is interpreted as text and not as control sequence. e.g. 
currently `logs@w3 . @w3 . @w3 . @p6\n` -> `finden @w3. @w3. @w3. @p6\n`

```csv
dialogue_WaeW8,"<b><color=#0088ff>@q0[ {PlayerName} ]</color></b>\n<color=#8ab3ff>Alright Ida, Spill it!@w2</color>\n<color=#8ab3ff>What's going on with this job for Polaris?</color>@p9\n<b><color=#ffee00>[ IDA ]</color></b>\n<color=#00e1ff>It is a delicate situation, Commander.@w2</color>\n<color=#00e1ff>I would like you to confirm the condition of the Communication Relay satellites before I give you a definitive answer.@w3</color>\n<color=#00e1ff>I would prefer to avoid alarming you if possible.</color>@p9\n<b><color=#0088ff>[ {PlayerName} ]</color></b>\n<color=#8ab3ff>Bull. Shit.@w4</color>\n<color=#8ab3ff>You're up to something and you're not telling me because I either won't like it or wouldn't agree with whatever it is you're doing behind my back.</color>@p9\n<b><color=#0088ff>[ {PlayerName} ]</color></b>\n<color=#8ab3ff>You've been steering me towards this station as soon as we heard about it and now the job that has nothing to do with finding the rest of the fleet!@w3</color>\n<color=#8ab3ff>What am I supposed to think?@w3 Were you infected with that Polaris malware they tried to slip you after all?@p9</color>\n<b><color=#ffee00>[ IDA ]</color></b>\n<color=#00e1ff>Commander...@w2</color>\n<color=#00e1ff>I believe that I will be able to access Polaris' Secure Systems through their Communications Relay and obtain the secure data from their systems.@w2 That is why I had to undertake this mission for Polaris.</color>","<b><color=#0088ff>@q0[ {PlayerName} ]</color></b>\n<color=#8ab3ff>Alles klar, Ida, spuck es aus!@w2</color>\n<color=#8ab3ff>Was ist mit diesem Job für Polaris los?</color>@p9\n<b><color=#ffee00>[ IDA ]</color></b>\n<color=#00e1ff>Es ist eine heikle Situation, Kommandant.@w2</color>\n<color=#00e1ff>Ich möchte Sie bitten, den Zustand der Communication Relay-Satelliten zu bestätigen, bevor ich Ihnen eine endgültige Antwort gebe.@w3</color>\n<color=#00e1ff>Ich würde es nach Möglichkeit lieber vermeiden, Sie zu beunruhigen.</color>@p9\n<b><color=#0088ff>[ {PlayerName} ]</color></b>\n<color=#8ab3ff>Bull. Scheiße.@w4</color>\n<color=#8ab3ff>Du hast etwas vor und sagst es mir nicht, weil es mir entweder nicht gefällt oder ich mit dem, was du hinter meinem Rücken tust, nicht einverstanden bin.</color>@p9\n<b><color=#0088ff>[ {PlayerName} ]</color></b>\n<color=#8ab3ff>Sie haben mich sofort zu dieser Station geführt, als wir davon hörten, und jetzt geht es um die Aufgabe, die nichts damit zu tun hat, den Rest der Flotte zu finden!@w3</color>\n<color=#8ab3ff>Was soll ich denken?@w3 Waren Sie mit der Polaris-Malware infiziert, die man Ihnen doch entlocken wollte? @p9</color>\n<b><color=#ffee00>[ IDA ]</color></b>\n<color=#00e1ff>Kommandant...@w2</color>\n<color=#00e1ff>Ich glaube, dass ich über ihr Kommunikationsrelais auf die sicheren Systeme von Polaris zugreifen und die sicheren Daten von ihren Systemen erhalten kann.@w2 Deshalb musste ich diese Mission für Polaris übernehmen.</color>",,,,,,,,,,,,,,,
```
