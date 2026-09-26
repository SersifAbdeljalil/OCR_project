"""
schemas.py - Modele Pydantic du JSON de sortie (schema unique).

Chaque document traite produit un JSON de cette forme :
    {"type", "source", "date_traitement", "confiance_classification",
     "champs": {...selon le type...}, "necessite_validation_humaine", "texte_brut"}

Pydantic verifie automatiquement les types (nombre, date, booleen...) et nos
regles supplementaires (champs autorises selon la categorie, coherence avec
le seuil de confiance).

CONFIDENTIALITE : les messages d'erreur ne recopient jamais les valeurs
(hide_input_in_errors), et l'affichage d'un objet (repr) masque champs et texte.
"""

# --- Imports ---------------------------------------------------------------
import json
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional, Union

from pydantic import (BaseModel, ConfigDict, Field, ValidationInfo,
                      field_validator, model_validator)

from src.config import (MOTIF_NOM, SEUIL_CONFIANCE, champs_attendus,
                        registre_par_defaut)

# Une valeur extraite : texte, nombre, montant exact (Decimal, venant de
# normalize.py) ou None (absente du document)
Valeur = Optional[Union[str, int, float, Decimal]]
CENTIME = Decimal("0.01")


def _vers_json(valeur, indent: int, niveau: int = 0) -> str:
    """Encode en JSON comme json.dumps, SAUF les Decimal, ecrits comme
    nombres a 2 decimales (240.50 et non "240.50" ni 240.5)."""
    if isinstance(valeur, Decimal):
        return str(valeur.quantize(CENTIME, rounding=ROUND_HALF_UP))
    if isinstance(valeur, dict):
        if not valeur:
            return "{}"
        marge, marge_fin = " " * indent * (niveau + 1), " " * indent * niveau
        lignes = [f"{marge}{json.dumps(str(cle), ensure_ascii=False)}: "
                  f"{_vers_json(v, indent, niveau + 1)}" for cle, v in valeur.items()]
        return "{\n" + ",\n".join(lignes) + "\n" + marge_fin + "}"
    return json.dumps(valeur, ensure_ascii=False)   # texte, nombre, booleen, None


class DocumentSortie(BaseModel):
    """Le JSON produit pour UN document."""

    model_config = ConfigDict(
        extra="forbid",              # aucune cle en dehors du schema unique
        hide_input_in_errors=True,   # jamais de valeur recopiee dans une erreur
    )

    # --- Les 7 cles du schema unique ---
    type: str                                    # nom de categorie normalise
    source: str = Field(min_length=1)            # nom du fichier d'origine
    date_traitement: datetime
    confiance_classification: float = Field(ge=0.0, le=1.0)
    champs: dict[str, Valeur] = Field(repr=False)   # repr=False : jamais affiche
    necessite_validation_humaine: bool
    texte_brut: str = Field(repr=False)

    # --- Regle 1 : le type est un nom normalise ("factures", "bulletin_paie") ---
    @field_validator("type")
    @classmethod
    def _type_normalise(cls, valeur: str) -> str:
        if not MOTIF_NOM.match(valeur):
            raise ValueError("le type doit etre en minuscules sans accents (a-z 0-9 _)")
        return valeur

    # --- Regles 2 et 3 : champs autorises + coherence avec le seuil ---
    @model_validator(mode="after")
    def _verifier_coherence(self, info: ValidationInfo) -> "DocumentSortie":
        # Le registre peut etre fourni (tests) ; sinon on prend celui du projet.
        registre = (info.context or {}).get("registre") or registre_par_defaut()
        attendus = champs_attendus(registre, self.type)

        # Un champ non prevu pour ce type = erreur (on donne son NOM, pas sa valeur)
        inconnus = sorted(set(self.champs) - set(attendus))
        if inconnus:
            raise ValueError(f"champs non prevus pour le type {self.type!r} : {inconnus}")

        # Tous les champs attendus sont presents, dans l'ordre du registre ;
        # ceux que le document ne contient pas valent None.
        self.champs = {nom: self.champs.get(nom) for nom in attendus}

        # Sous le seuil, le document DOIT passer par la validation humaine...
        # ... sauf s'il vient justement d'etre valide par l'humain (validation.py).
        valide_par_humain = (info.context or {}).get("valide_par_humain", False)
        if (self.confiance_classification < SEUIL_CONFIANCE
                and not self.necessite_validation_humaine and not valide_par_humain):
            raise ValueError(f"confiance < {SEUIL_CONFIANCE} : "
                             "necessite_validation_humaine doit valoir true")
        return self

    # --- Ecriture du fichier .json ---
    def vers_json(self, supplement: dict = None, indent: int = 2) -> str:
        """Texte JSON du document (UTF-8, accents lisibles). Les montants Decimal
        sont ecrits comme nombres a 2 decimales : 240.50.
        `supplement` : cles ajoutees apres le schema unique (ex. pour A_Valider/ :
        raison, alertes, categorie_proposee)."""
        donnees = self.model_dump(mode="json")   # date -> texte ISO, etc.
        donnees["champs"] = self.champs          # garde les Decimal intacts
        donnees.update(supplement or {})
        return _vers_json(donnees, indent)
