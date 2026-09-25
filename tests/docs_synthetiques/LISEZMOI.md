# Factures de test FICTIVES (jeu synthétique)

Toutes les données sont inventées : sociétés, ICE, RIB (code banque 999, inexistant), numéros, montants.
Aucune donnée réelle : ces fichiers peuvent être lus par Claude Code et versionnés dans git.

À placer dans : tests/docs_synthetiques/   (PAS dans tests/docs_test/, réservé aux documents réels)

| Fichier | Format | Piège testé |
|---|---|---|
| f01_fr_standard_natif.pdf | PDF natif | cas de référence, TVA 20 % |
| f02_fr_multi_tva_natif.pdf | PDF natif | 4 taux de TVA (20/14/10/7), date « 1er août 2026 » |
| f03_fr_sans_tva_natif.pdf | PDF natif | pas de TVA (HT = TTC), « Net à payer », date 30.09.2026 |
| f04_fr_ecart_totaux_natif.pdf | PDF natif | TTC faux de 100,00 : doit partir en validation |
| f05_bilingue_natif.pdf | PDF natif | libellés français + arabe (~44 % de lettres arabes) |
| f06_arabe_natif.pdf | PDF natif | facture presque entièrement en arabe |
| f07_fr_scan_propre.jpg | image | scan propre, légèrement penché |
| f08_fr_scan_degrade.pdf | PDF image | flou, bruit, penché, tampon « PAYÉ », année sur 2 chiffres |
| f09_bilingue_scan.pdf | PDF image | scan bilingue : l'arabe fait baisser la confiance OCR |
| f10_fr_montants_europeens_natif.pdf | PDF natif | montants 5.455,00, date en lettres, « Net à payer TTC » |

verite_terrain.json donne, pour chaque fichier, les valeurs exactes attendues
(fournisseur, date ISO, numéro, HT, TVA, TTC, ICE) et la décision attendue.
Il permet de mesurer l'exactitude de l'extraction champ par champ.

Points techniques révélés par ces fichiers :
- Dans les PDF arabes, le texte extrait est en « formes de présentation » (U+FB50 à U+FEFF),
  souvent dans l'ordre visuel inversé. La détection de l'arabe doit compter ces caractères,
  pas seulement la plage U+0600 à U+06FF.
- f05 : une facture bilingue marocaine dépasse facilement 30 % de lettres arabes.
  Avec la règle actuelle, elle partirait en validation alors que tout est lisible en français.
