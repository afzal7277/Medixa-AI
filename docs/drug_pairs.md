# Medixa AI — Reference Drug Pairs by Severity Level

Five curated drug pairs that exercise every severity class in the model.
Use these for manual QA, demo scenarios, or smoke-testing the UI.

---

## 1. Contraindicated

| Field | Value |
|---|---|
| **Drug A** | Warfarin |
| **Drug B** | Aspirin |
| **Severity** | Contraindicated |
| **Mechanism** | Warfarin inhibits vitamin K–dependent clotting factors (CYP2C9); aspirin irreversibly inhibits platelet COX-1. Combined, they drastically increase bleeding risk with no safe therapeutic window for most patients. |
| **Evidence** | Black-box FDA warning; multiple fatal haemorrhage case reports in OpenFDA FAERS. |
| **Clinical action** | Avoid combination. If antiplatelet therapy is essential, use the lowest aspirin dose under close INR monitoring with specialist oversight. |
| **Expected model output** | `severity: "Contraindicated"`, confidence ≥ 0.80 |

---

## 2. Severe

| Field | Value |
|---|---|
| **Drug A** | Methotrexate |
| **Drug B** | Trimethoprim |
| **Severity** | Severe |
| **Mechanism** | Both drugs inhibit dihydrofolate reductase (DHFR), causing additive folate antagonism. Combined use has caused severe pancytopenia and life-threatening bone marrow suppression. |
| **Evidence** | Hospitalisation and death reports in FAERS; classified as a major interaction in all major drug databases. |
| **Clinical action** | Avoid concurrent use. If unavoidable, use leucovorin rescue and weekly CBC monitoring. |
| **Expected model output** | `severity: "Severe"`, confidence ≥ 0.72 |

---

## 3. Moderate

| Field | Value |
|---|---|
| **Drug A** | Fluoxetine |
| **Drug B** | Tramadol |
| **Severity** | Moderate |
| **Mechanism** | Fluoxetine inhibits CYP2D6, reducing tramadol conversion to active metabolite O-desmethyltramadol and increasing serotonergic load. Risk of serotonin syndrome and reduced analgesia. |
| **Evidence** | Multiple case reports of serotonin syndrome; FDA drug interaction label update 2019. |
| **Clinical action** | Monitor closely for serotonin syndrome signs (hyperthermia, clonus, agitation). Consider alternative analgesic. |
| **Expected model output** | `severity: "Moderate"`, confidence ≥ 0.65 |

---

## 4. Mild

| Field | Value |
|---|---|
| **Drug A** | Atorvastatin |
| **Drug B** | Amlodipine |
| **Severity** | Mild |
| **Mechanism** | Amlodipine is a weak CYP3A4 inhibitor that can modestly raise atorvastatin plasma levels (~15%). The increase is below the threshold that meaningfully raises myopathy risk at standard doses. |
| **Evidence** | PK studies show AUC increase; FDA label recommends caution at high atorvastatin doses but no contraindication at ≤ 40 mg. |
| **Clinical action** | Generally safe. Limit atorvastatin to 40 mg/day when combined; monitor for myalgia. |
| **Expected model output** | `severity: "Mild"`, confidence ≥ 0.60 |

---

## 5. No Interaction

| Field | Value |
|---|---|
| **Drug A** | Metformin |
| **Drug B** | Lisinopril |
| **Severity** | None |
| **Mechanism** | Metformin is renally excreted unchanged (no CYP involvement); lisinopril is also renally cleared with no CYP metabolism. No shared metabolic pathway, transporter, or pharmacodynamic overlap. |
| **Evidence** | No clinically significant interaction found in FAERS; routinely co-prescribed in diabetic hypertension management. |
| **Clinical action** | No special precautions required. Standard renal function monitoring as per each drug's label. |
| **Expected model output** | `severity: "None"`, confidence ≥ 0.75 |

---

## Usage in Medixa AI UI

```
Drug A: warfarin      Drug B: aspirin       → Contraindicated (red)
Drug A: methotrexate  Drug B: trimethoprim  → Severe (orange)
Drug A: fluoxetine    Drug B: tramadol      → Moderate (amber)
Drug A: atorvastatin  Drug B: amlodipine    → Mild (yellow)
Drug A: metformin     Drug B: lisinopril    → None (green)
```

These five pairs cover the full severity spectrum and rely on well-established
pharmacological mechanisms, making them reliable demo and regression fixtures.
