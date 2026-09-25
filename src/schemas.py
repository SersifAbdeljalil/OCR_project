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
from datetime import datetime
from typing import Optional, Union

from pydantic import (BaseModel, ConfigDict, Field, ValidationInfo,
                      field_validator, model_validator)

from src.config import (MOTIF_NOM, SEUIL_CONFIANCE, champs_attendus,
                        registre_par_defaut)

# Une valeur extraite : texte, nombre (montants) ou None (absente du document)
Valeur = Optional[Union[str, int, float]]


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

        # Sous le seuil, le document DOIT passer par la validation humaine.
        if (self.confiance_classification < SEUIL_CONFIANCE
                and not self.necessite_validation_humaine):
            raise ValueError(f"confiance < {SEUIL_CONFIANCE} : "
                             "necessite_validation_humaine doit valoir true")
        return self
