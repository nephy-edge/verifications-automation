# Loan-tape column survey

Generated 2026-09-03 against all 46 `dbt_source.*_source_loans_staging` tables (one `SELECT * LIMIT 1` per borrower, headers only). Backs `config.yaml`'s `loan_tape_columns` section — see that file's comments for how the mapping is resolved and used.

**Headline: only 1 of 46 borrowers (`advance`) matches the app's canonical field names exactly on every money/date field that drives the actual disbursement/collection math in `normalize_loan_tape_row()`.** Everyone else needs at least one override to be correctly represented, or is silently missing that figure today.

**Bug fixed alongside this survey:** `normalize_loan_tape_row()` looked for `fees_outstanding`/`penalties_outstanding` (plural) while `_loan_tape_summary()` (the UI tiles) used `fee_outstanding`/`penalty_outstanding` (singular) — real data (`leasy`, the most-used test borrower, included) uses the singular form. The plural form was never real; it silently zeroed fee/penalty outstanding out of every collections calculation, inflating the derived "collected to date" figure. `config.yaml`'s default now uses the singular (real) names.

## Safe overrides already applied

Unambiguous — the only candidate column, differing purely by underscore/spacing, nothing else it could plausibly be:

- `mkopa`: `loan_id` -> `loanid`
- `mottu`: `begin_date` -> `begindate`
- `samunnati`: `principal_outstanding` -> `principaloutstanding`
- `sary`: `loan_id` -> `loanid`
- `sary`: `days_past_due` -> `dayspastdue`

## Needs a business call before it can go in config.yaml

Grouped by field. Each row is a borrower missing that field under its canonical name, with the real columns that could plausibly be it — picking the wrong one silently misstates a real dollar figure, so none of these were guessed into config.yaml. `(none)` means no plausible candidate was found at all (the borrower's data may not carry that concept, e.g. non-amortizing products).

### `loan_id`

| Borrower | Candidates |
|---|---|
| credismart | `restruture_from_loan_id`, `restruture_to_loan_id`, `loan_amount`, `loan_disbursement_date`, `loan_due_date`, `loan_status` |
| dlight | (none) |
| koinworks | `id`, `is_top_up_loan`, `loan_code` |
| lhoopa | `pipeline_run_id`, `property_id` |
| moladin | `active_loan_id`, `loan_submission_id`, `refinanced_rolled_over_loan_id`, `account_id`, `external_asset_id`, `loan_purpose` |
| mottu | `pipeline_run_id` |
| mufin | `loan_amount`, `loan_date`, `loan_number`, `pipeline_run_id` |
| payjoy | `pipeline_run_id` |
| payjoy_full | (none) |
| payjoy_secured | `pipeline_run_id` |
| samunnati | `refinanced_loan_id`, `pipeline_run_id`, `type_of_loan` |
| shara | `id`, `user_id` |
| solar_panda | `customer_id`, `sale_id` |
| watu_africa | `external_loan_id`, `external_account_id`, `loan_status`, `loan_type`, `pipeline_run_id` |

### `begin_date`

| Borrower | Candidates |
|---|---|
| credismart | `loan_disbursement_date`, `loan_due_date`, `schedule_installment_date`, `total_principal_interest_and_other_charges_received_to_date` |
| dlight | `end_date` |
| first_circle | `disbursement_date` |
| first_digital_finance_corporation | `client_activation_date`, `closed_date`, `disbursed_date`, `expected_maturity_date`, `first_due_date`, `last_payment_date` |
| koinworks | `cleared_date`, `disbursed_date` |
| lhoopa | `noa_date`, `property_purchase_date`, `pulled_out_date`, `submitted_date` |
| metafin | `emi_begin_date`, `date_restructured`, `emi_end_date`, `iot_installation_date`, `revised_end_date` |
| mkopa | (none) |
| moladin | `dpd0_date`, `end_date`, `latest_extension_date`, `latest_restructure_date`, `start_date`, `updated_end_date` |
| moladin_property | `end_date`, `restructure_date`, `start_date`, `updated_end_date` |
| mufin | `date_of_first_installment`, `date_of_last_installment`, `date_of_repossession`, `dpd_on_date_of_repossesssion`, `last_receipt_date`, `loan_date` |
| payjoy | `origination_date` |
| payjoy_ecuador | `date`, `expected_maturity_date`, `last_expected_payment_due_date` |
| payjoy_full | `origination_date` |
| payjoy_sa | `date`, `expected_maturity_date`, `last_expected_payment_due_date` |
| payjoy_secured | `origination_date` |
| samunnati | `report_date`, `repossession_date` |
| sary | `due_date`, `start_date` |
| solar_panda | `end_date`, `installed_date` |
| validus_id | `disbursement_date`, `maturity_date`, `repayment_date` |
| validus_id_full | `disbursement_date`, `maturity_date`, `repayment_date` |
| validus_id_lendable | `disbursement_date`, `maturity_date` |
| validus_id_secured | `disbursement_date`, `maturity_date` |

### `principal_amount`

| Borrower | Candidates |
|---|---|
| amartha | `principal_amount_lendable`, `principal_amount_loan`, `expected_payment_amount_loan_per_installment`, `principal_remaining_lendable`, `principal_remaining_loan` |
| amartha2 | `principal_amount_lendable`, `principal_amount_loan`, `expected_payment_amount_loan_per_installment`, `outstanding_amount_lendable`, `outstanding_amount_loan`, `principal_due_at_cutoff_loan` |
| amartha_full | `principal_amount_lendable`, `principal_amount_loan`, `expected_payment_amount_loan_per_installment`, `principal_remaining_lendable`, `principal_remaining_loan` |
| amartha_full_portfolio | `principal_amount_lendable`, `principal_amount_loan`, `expected_payment_amount_loan_per_installment`, `principal_remaining_lendable`, `principal_remaining_loan` |
| amartha_secured | `principal_amount_lendable`, `principal_amount_loan`, `expected_payment_amount_loan_per_installment`, `outstanding_amount_lendable`, `outstanding_amount_loan`, `principal_due_at_cutoff_loan` |
| credismart | `loan_amount`, `principal`, `principal_still_outstanding`, `total_principal_interest_and_other_charges_received_to_date` |
| dlight | (none) |
| exitus | `principal`, `principal_outstanding` |
| first_circle | `loan_amount` |
| first_digital_finance_corporation | `last_payment_amount`, `principal`, `principal_outstanding`, `principal_overdue`, `principal_repaid` |
| khazna | `priciple_amount`, `principal_outstanding`, `principal_remaining`, `total_loan_amount` |
| koinworks | `funding_amount`, `principal_remaining` |
| lendmn | `principal`, `principal_outstanding` |
| lendmn_micro | `principal`, `principal_outstanding` |
| lendmn_revolving | `principal`, `principal_outstanding` |
| lhoopa | `acquisition_tax_amount`, `actual_construction_amount_paid`, `property_purchase_amount`, `refurbishment_amount` |
| mkopa | (none) |
| moladin | `amount`, `principal_remaining` |
| moladin_property | `amount__principal___interest___origination_fee_`, `principal_remaining` |
| mottu | (none) |
| mufin | `over_due_principal_amount_as_on_december_31_2025`, `installment_amount`, `insurance_amount`, `loan_amount`, `over_due_interest_amount_as_on_december_31_2025`, `principal_outstanding_as_on_december_31_2025` |
| payjoy | `amount_120_plus`, `amount_31_to_60`, `amount_3_to_30`, `amount_61_to_90`, `amount_91_to_120`, `amount_current_to_2` |
| payjoy_ecuador | `principal_of_loan`, `principal_outstanding` |
| payjoy_full | `amount_120_plus`, `amount_31_to_60`, `amount_3_to_30`, `amount_61_to_90`, `amount_91_to_120`, `amount_current_to_2` |
| payjoy_sa | `principal_of_loan`, `principal_outstanding` |
| payjoy_secured | `amount_120_plus`, `amount_31_to_60`, `amount_3_to_30`, `amount_61_to_90`, `amount_91_to_120`, `amount_current_to_2` |
| samunnati | `netpay_off_amount`, `total_disbursed_amount`, `writeoff_amount` |
| sary | `invoice_amount` |
| shara | `down_payment_amount`, `principal`, `principal_outstanding` |
| solar_panda | (none) |
| sugmya_finance | `principal`, `principal_collected`, `principal_outstanding` |
| techcoop | `principal` |
| validus_id | `actual_interest_amount` |
| validus_id_full | `actual_interest_amount` |
| validus_id_lendable | `interest_amount`, `loan_amount` |
| validus_id_secured | `interest_amount`, `loan_amount` |
| watu_africa | `principal`, `principal_outstanding` |

### `total_loan_amount`

| Borrower | Candidates |
|---|---|
| amartha | `expected_payment_amount_loan_per_installment`, `principal_amount_loan`, `loan_id`, `loan_purpose`, `origin_loan_id`, `principal_amount_lendable` |
| amartha2 | `expected_payment_amount_loan_per_installment`, `outstanding_amount_loan`, `principal_amount_loan`, `loan_id`, `loan_purpose`, `outstanding_amount_lendable` |
| amartha_full | `expected_payment_amount_loan_per_installment`, `principal_amount_loan`, `loan_id`, `loan_purpose`, `origin_loan_id`, `principal_amount_lendable` |
| amartha_full_portfolio | `expected_payment_amount_loan_per_installment`, `principal_amount_loan`, `loan_id`, `loan_purpose`, `origin_loan_id`, `principal_amount_lendable` |
| amartha_secured | `expected_payment_amount_loan_per_installment`, `outstanding_amount_loan`, `principal_amount_loan`, `loan_id`, `loan_purpose`, `outstanding_amount_lendable` |
| credismart | `loan_amount`, `loan_disbursement_date`, `loan_due_date`, `loan_status`, `loan_use`, `restruture_from_loan_id` |
| dlight | (none) |
| exitus | `expected_total_interest`, `loan_id`, `loan_status`, `refinanced_loan_id` |
| f88 | `loan_id`, `loan_purpose`, `loan_type_tag`, `principal_amount` |
| first_circle | `loan_amount`, `loan_id`, `loan_type`, `restructure_from_loan_id`, `restructure_to_loan_id` |
| first_digital_finance_corporation | `client_total_loans`, `last_payment_amount`, `loan_id`, `loan_number`, `loan_number_adjusted`, `loan_status` |
| koinworks | `funding_amount`, `is_top_up_loan`, `loan_code`, `total_commission_fee` |
| lendmn | `expected_total_interest`, `extended_loan_id`, `loan_id` |
| lendmn_micro | `expected_total_interest`, `extended_loan_id`, `loan_created_at`, `loan_id` |
| lendmn_revolving | `expected_total_interest`, `extended_loan_id`, `loan_id` |
| lhoopa | `acquisition_tax_amount`, `actual_construction_amount_paid`, `property_purchase_amount`, `refurbishment_amount` |
| metafin | `loan_id`, `loan_status`, `principal_amount` |
| mkopa | (none) |
| moladin | `active_loan_id`, `amount`, `loan_purpose`, `loan_status`, `loan_submission_id`, `refinanced_rolled_over_loan_id` |
| moladin_property | `amount__principal___interest___origination_fee_`, `loan_id`, `loan_purpose`, `loan_type`, `refinanced_rolled_over_loan_id` |
| mottu | (none) |
| mufin | `loan_amount`, `installment_amount`, `insurance_amount`, `loan_date`, `loan_number`, `over_due_interest_amount_as_on_december_31_2025` |
| payjoy | `amount_120_plus`, `amount_31_to_60`, `amount_3_to_30`, `amount_61_to_90`, `amount_91_to_120`, `amount_current_to_2` |
| payjoy_ecuador | `current_loan_status`, `loan_id`, `loan_product`, `loan_tenor_in_months`, `principal_of_loan`, `size_of_loan` |
| payjoy_full | `amount_120_plus`, `amount_31_to_60`, `amount_3_to_30`, `amount_61_to_90`, `amount_91_to_120`, `amount_current_to_2` |
| payjoy_sa | `current_loan_status`, `loan_id`, `loan_product`, `loan_tenor_in_months`, `principal_of_loan`, `size_of_loan` |
| payjoy_secured | `amount_120_plus`, `amount_31_to_60`, `amount_3_to_30`, `amount_61_to_90`, `amount_91_to_120`, `amount_current_to_2` |
| samunnati | `total_disbursed_amount`, `netpay_off_amount`, `refinanced_loan_id`, `type_of_loan`, `writeoff_amount` |
| sary | `invoice_amount` |
| shara | `down_payment_amount`, `expected_total_interest` |
| solar_panda | (none) |
| sugmya_finance | `expected_total_interest`, `loan_id`, `refinanced_loan_id` |
| techcoop | `expected_total_interest`, `loan_id` |
| validus_id | `total_loan`, `actual_interest_amount`, `loan_id` |
| validus_id_full | `total_loan`, `actual_interest_amount`, `loan_id` |
| validus_id_lendable | `loan_amount`, `interest_amount`, `loan_id` |
| validus_id_secured | `loan_amount`, `interest_amount`, `loan_id` |
| watu_africa | `external_loan_id`, `loan_status`, `loan_type` |

### `principal_outstanding`

| Borrower | Candidates |
|---|---|
| amartha | `principal_amount_lendable`, `principal_amount_loan`, `principal_remaining_lendable`, `principal_remaining_loan` |
| amartha2 | `outstanding_amount_lendable`, `outstanding_amount_loan`, `principal_amount_lendable`, `principal_amount_loan`, `principal_due_at_cutoff_loan`, `principal_paid_loan` |
| amartha_full | `principal_amount_lendable`, `principal_amount_loan`, `principal_remaining_lendable`, `principal_remaining_loan` |
| amartha_full_portfolio | `principal_amount_lendable`, `principal_amount_loan`, `principal_remaining_lendable`, `principal_remaining_loan` |
| amartha_secured | `outstanding_amount_lendable`, `outstanding_amount_loan`, `principal_amount_lendable`, `principal_amount_loan`, `principal_due_at_cutoff_loan`, `principal_paid_loan` |
| autocheck__ci | `principal_amount`, `principal_out`, `principal_remaining` |
| autocheck__ug | `principal_amount`, `principal_out`, `principal_remaining` |
| credismart | `principal_still_outstanding`, `interest_still_outstanding`, `other_fees_and_charges_still_outstanding`, `principal`, `total_principal_interest_and_other_charges_received_to_date` |
| dlight | (none) |
| first_circle | (none) |
| koinworks | `principal_remaining` |
| lhoopa | (none) |
| metafin | `principal_amount` |
| mkopa | (none) |
| moladin | `principal_remaining` |
| moladin_property | `amount__principal___interest___origination_fee_`, `principal_remaining` |
| mottu | (none) |
| mufin | `principal_outstanding_as_on_december_31_2025`, `principal_outstanding_on_date_of_repossession`, `over_due_principal_amount_as_on_december_31_2025`, `principal_received`, `principal_settled` |
| sary | (none) |
| solar_panda | (none) |
| techcoop | `principal` |
| validus_id | (none) |
| validus_id_full | (none) |
| validus_id_lendable | (none) |
| validus_id_secured | (none) |

### `interest_outstanding`

| Borrower | Candidates |
|---|---|
| amartha | `interest_period`, `interest_rate` |
| amartha2 | `interest_period`, `interest_rate`, `outstanding_amount_lendable`, `outstanding_amount_loan` |
| amartha_full | `interest_period`, `interest_rate` |
| amartha_full_portfolio | `interest_period`, `interest_rate` |
| amartha_secured | `interest_period`, `interest_rate`, `outstanding_amount_lendable`, `outstanding_amount_loan` |
| autocheck__ci | `interest_out`, `interest_period`, `interest_rate` |
| autocheck__ug | `interest_out`, `interest_period`, `interest_rate` |
| credismart | `interest_still_outstanding`, `gross_monthly_interest_rate`, `interest`, `other_fees_and_charges_still_outstanding`, `principal_still_outstanding`, `total_principal_interest_and_other_charges_received_to_date` |
| dlight | (none) |
| exitus | `expected_total_interest`, `interest_rate`, `interest_rate_period`, `interest_rate_type`, `principal_outstanding` |
| first_circle | `effective_interest_rate` |
| koinworks | `interest_remaining`, `is_interest_margin` |
| lendmn | `expected_total_interest`, `interest_rate`, `interest_rate_period`, `interest_rate_type`, `principal_outstanding` |
| lendmn_micro | `expected_total_interest`, `interest_rate`, `interest_rate_period`, `interest_rate_type`, `principal_outstanding` |
| lendmn_revolving | `expected_total_interest`, `interest_rate`, `interest_rate_period`, `interest_rate_type`, `principal_outstanding` |
| lhoopa | (none) |
| metafin | (none) |
| mkopa | (none) |
| moladin | `interest_period`, `interest_rate`, `interest_type` |
| moladin_property | `amount__principal___interest___origination_fee_`, `interest_period`, `interest_rate`, `interest_type` |
| mottu | (none) |
| mufin | `impairment_recorded_on_repossession_through_interest_reversal`, `interest_received`, `interest_settled`, `over_due_interest_amount_as_on_december_31_2025`, `principal_outstanding_as_on_december_31_2025`, `principal_outstanding_on_date_of_repossession` |
| payjoy | `principal_outstanding` |
| payjoy_full | `principal_outstanding` |
| payjoy_secured | `principal_outstanding` |
| samunnati | (none) |
| sary | (none) |
| shara | `expected_total_interest`, `principal_outstanding` |
| solar_panda | (none) |
| sugmya_finance | `expected_total_interest`, `interest_rate`, `interest_rate_period`, `interest_rate_type`, `principal_outstanding` |
| techcoop | `expected_total_interest` |
| validus_id | `actual_interest_amount`, `interest_per_annum` |
| validus_id_full | `actual_interest_amount`, `interest_per_annum` |
| validus_id_lendable | `interest_amount`, `interest_per_annum` |
| validus_id_secured | `interest_amount`, `interest_per_annum` |

### `fee_outstanding`

| Borrower | Candidates |
|---|---|
| advance | `fees_outstanding`, `interest_outstanding`, `penalties_outstanding`, `principal_outstanding` |
| amartha | (none) |
| amartha2 | `outstanding_amount_lendable`, `outstanding_amount_loan` |
| amartha_full | (none) |
| amartha_full_portfolio | (none) |
| amartha_secured | `outstanding_amount_lendable`, `outstanding_amount_loan` |
| autocheck__ci | (none) |
| autocheck__ug | (none) |
| credismart | `interest_still_outstanding`, `other_fees_and_charges_still_outstanding`, `principal_still_outstanding` |
| dlight | (none) |
| exitus | `principal_outstanding`, `upfront_fee` |
| first_circle | `origination_fee` |
| first_digital_finance_corporation | `fees_outstanding`, `interest_outstanding`, `penalties_outstanding`, `principal_outstanding` |
| koinworks | `total_commission_fee` |
| lendmn | `principal_outstanding` |
| lendmn_micro | `principal_outstanding` |
| lendmn_revolving | `principal_outstanding` |
| lhoopa | (none) |
| metafin | `processing_fee` |
| mkopa | (none) |
| moladin | `origination_fee` |
| moladin_property | `amount__principal___interest___origination_fee_`, `origination_fee__upfront_fee___discount_fee_` |
| mottu | (none) |
| mufin | `principal_outstanding_as_on_december_31_2025`, `principal_outstanding_on_date_of_repossession` |
| payjoy | `principal_outstanding` |
| payjoy_ecuador | `interest_outstanding`, `principal_outstanding` |
| payjoy_full | `principal_outstanding` |
| payjoy_sa | `interest_outstanding`, `principal_outstanding` |
| payjoy_secured | `principal_outstanding` |
| samunnati | `early_paid_off_fee`, `processing_fee` |
| sary | (none) |
| shara | `principal_outstanding` |
| solar_panda | (none) |
| sugmya_finance | `principal_outstanding`, `upfront_fee` |
| techcoop | (none) |
| validus_id | (none) |
| validus_id_full | (none) |
| validus_id_lendable | (none) |
| validus_id_secured | (none) |

### `penalty_outstanding`

| Borrower | Candidates |
|---|---|
| advance | `fees_outstanding`, `interest_outstanding`, `penalties_outstanding`, `principal_outstanding` |
| amartha | (none) |
| amartha2 | `outstanding_amount_lendable`, `outstanding_amount_loan` |
| amartha_full | (none) |
| amartha_full_portfolio | (none) |
| amartha_secured | `outstanding_amount_lendable`, `outstanding_amount_loan` |
| autocheck__ci | `penalty_out` |
| autocheck__ug | `penalty_out` |
| credismart | `interest_still_outstanding`, `other_fees_and_charges_still_outstanding`, `principal_still_outstanding` |
| dlight | (none) |
| exitus | `principal_outstanding` |
| first_circle | (none) |
| first_digital_finance_corporation | `fees_outstanding`, `interest_outstanding`, `penalties_outstanding`, `principal_outstanding` |
| khazna | `fee_outstanding`, `interest_outstanding`, `principal_outstanding` |
| koinworks | (none) |
| lendmn | `principal_outstanding` |
| lendmn_micro | `principal_outstanding` |
| lendmn_revolving | `principal_outstanding` |
| lhoopa | (none) |
| metafin | (none) |
| mkopa | (none) |
| moladin | (none) |
| moladin_property | (none) |
| mottu | (none) |
| mufin | `principal_outstanding_as_on_december_31_2025`, `principal_outstanding_on_date_of_repossession` |
| payjoy | `principal_outstanding` |
| payjoy_ecuador | `interest_outstanding`, `principal_outstanding` |
| payjoy_full | `principal_outstanding` |
| payjoy_sa | `interest_outstanding`, `principal_outstanding` |
| payjoy_secured | `principal_outstanding` |
| samunnati | (none) |
| sary | (none) |
| shara | `principal_outstanding` |
| solar_panda | (none) |
| sugmya_finance | `principal_outstanding` |
| techcoop | (none) |
| validus_id | (none) |
| validus_id_full | (none) |
| validus_id_lendable | (none) |
| validus_id_secured | (none) |
| watu_africa | `fee_outstanding`, `interest_outstanding`, `principal_outstanding` |

### `closure_date`

| Borrower | Candidates |
|---|---|
| amartha | `begin_date`, `maturity_date` |
| amartha_full | `begin_date`, `cutoff_date`, `maturity_date` |
| amartha_full_portfolio | `begin_date`, `cutoff_date`, `maturity_date` |
| credismart | `loan_disbursement_date`, `loan_due_date`, `schedule_installment_date`, `total_principal_interest_and_other_charges_received_to_date` |
| dlight | `end_date` |
| exitus | `begin_date`, `default_date`, `end_date`, `payoff_date`, `repossession_date` |
| first_circle | `disbursement_date` |
| first_digital_finance_corporation | `client_activation_date`, `closed_date`, `disbursed_date`, `expected_maturity_date`, `first_due_date`, `last_payment_date` |
| koinworks | `cleared_date`, `disbursed_date` |
| lendmn | `begin_date`, `end_date`, `original_maturity_date`, `payoff_date` |
| lendmn_micro | `begin_date`, `end_date`, `original_maturity_date`, `payoff_date` |
| lendmn_revolving | `begin_date`, `end_date`, `original_maturity_date`, `payoff_date` |
| lhoopa | `noa_date`, `property_purchase_date`, `pulled_out_date`, `submitted_date` |
| metafin | `date_restructured`, `emi_begin_date`, `emi_end_date`, `iot_installation_date`, `revised_end_date` |
| mkopa | (none) |
| moladin | `dpd0_date`, `end_date`, `latest_extension_date`, `latest_restructure_date`, `start_date`, `updated_end_date` |
| moladin_property | `end_date`, `restructure_date`, `start_date`, `updated_end_date` |
| mottu | (none) |
| mufin | `date_of_first_installment`, `date_of_last_installment`, `date_of_repossession`, `dpd_on_date_of_repossesssion`, `last_receipt_date`, `loan_date` |
| payjoy | `origination_date` |
| payjoy_ecuador | `date`, `expected_maturity_date`, `last_expected_payment_due_date` |
| payjoy_full | `origination_date` |
| payjoy_sa | `date`, `expected_maturity_date`, `last_expected_payment_due_date` |
| payjoy_secured | `origination_date` |
| samunnati | `report_date`, `repossession_date` |
| sary | `due_date`, `start_date` |
| shara | `begin_date`, `end_date`, `payoff_date` |
| solar_panda | `end_date`, `installed_date` |
| techcoop | `begin_date`, `end_date` |
| validus_id | `disbursement_date`, `maturity_date`, `repayment_date` |
| validus_id_full | `disbursement_date`, `maturity_date`, `repayment_date` |
| validus_id_lendable | `disbursement_date`, `maturity_date` |
| validus_id_secured | `disbursement_date`, `maturity_date` |

### `company_due_date`

| Borrower | Candidates |
|---|---|
| amartha | `begin_date`, `maturity_date` |
| amartha2 | `begin_date`, `closure_date`, `days_past_due`, `maturity_date`, `principal_due_at_cutoff_loan` |
| amartha_full | `begin_date`, `cutoff_date`, `maturity_date` |
| amartha_full_portfolio | `begin_date`, `cutoff_date`, `maturity_date` |
| amartha_secured | `begin_date`, `closure_date`, `maturity_date`, `principal_due_at_cutoff_loan` |
| autocheck__ci | `begin_date`, `closure_date`, `date_repossession`, `date_resale`, `days_past_due`, `maturity_date` |
| autocheck__ug | `begin_date`, `closure_date`, `date_repossession`, `date_resale`, `days_past_due`, `maturity_date` |
| credismart | `loan_due_date`, `loan_disbursement_date`, `schedule_installment_date`, `total_principal_interest_and_other_charges_received_to_date` |
| dlight | `end_date` |
| exitus | `begin_date`, `default_date`, `end_date`, `payoff_date`, `repossession_date` |
| f88 | `begin_date`, `closure_date`, `days_past_due`, `maturity_date`, `original_maturity_date`, `write_off_date` |
| first_circle | `disbursement_date` |
| first_digital_finance_corporation | `first_due_date`, `client_activation_date`, `closed_date`, `days_past_due`, `days_past_due_band`, `disbursed_date` |
| khazna | `begin_date`, `birth_date`, `closure_date`, `company_name`, `days_past_due`, `maturity_date` |
| koinworks | `cleared_date`, `disbursed_date` |
| leasy | `begin_date`, `closure_date`, `days_past_due`, `maturity_date`, `original_maturity_date` |
| lendmn | `begin_date`, `end_date`, `original_maturity_date`, `payoff_date` |
| lendmn_micro | `begin_date`, `end_date`, `original_maturity_date`, `payoff_date` |
| lendmn_revolving | `begin_date`, `end_date`, `original_maturity_date`, `payoff_date` |
| lhoopa | `noa_date`, `property_purchase_date`, `pulled_out_date`, `submitted_date` |
| metafin | `date_restructured`, `emi_begin_date`, `emi_end_date`, `iot_installation_date`, `revised_end_date` |
| mkopa | (none) |
| moladin | `days_past_due`, `dpd0_date`, `end_date`, `latest_extension_date`, `latest_restructure_date`, `start_date` |
| moladin_property | `days_past_due`, `end_date`, `restructure_date`, `start_date`, `updated_end_date` |
| mottu | (none) |
| mufin | `date_of_first_installment`, `date_of_last_installment`, `date_of_repossession`, `dpd_on_date_of_repossesssion`, `last_receipt_date`, `loan_date` |
| payjoy | `origination_date` |
| payjoy_ecuador | `last_expected_payment_due_date`, `date`, `days_past_due`, `expected_maturity_date` |
| payjoy_full | `origination_date` |
| payjoy_sa | `last_expected_payment_due_date`, `date`, `days_past_due`, `expected_maturity_date` |
| payjoy_secured | `origination_date` |
| prestamype | `begin_date`, `closure_date`, `days_past_due`, `maturity_date`, `original_maturity_date`, `pledged_to_lendable_date` |
| r5 | `begin_date`, `closure_date`, `days_past_due`, `maturity_date`, `original_maturity_date`, `recovery_date` |
| samunnati | `report_date`, `repossession_date` |
| sary | `due_date`, `start_date` |
| shara | `begin_date`, `end_date`, `payoff_date` |
| solar_panda | `end_date`, `installed_date` |
| solvento | `begin_date`, `closure_date`, `days_past_due`, `lender_assignment_date`, `liquidation_date`, `maturity_date` |
| sugmya_finance | `begin_date`, `closure_date`, `default_date`, `end_date`, `payoff_date`, `repossession_date` |
| techcoop | `begin_date`, `end_date` |
| validus_id | `disbursement_date`, `maturity_date`, `repayment_date` |
| validus_id_full | `disbursement_date`, `maturity_date`, `repayment_date` |
| validus_id_lendable | `disbursement_date`, `maturity_date` |
| validus_id_secured | `disbursement_date`, `maturity_date` |
| watu_africa | `begin_date`, `closure_date`, `end_date`, `export_date`, `repossession_date` |

### `currency`

| Borrower | Candidates |
|---|---|
| advance | (none) |
| amartha | (none) |
| amartha2 | (none) |
| amartha_full | (none) |
| amartha_full_portfolio | (none) |
| amartha_secured | (none) |
| autocheck__ci | (none) |
| autocheck__ug | (none) |
| credismart | `income_local_currency` |
| dlight | `currency_type` |
| exitus | `currency_type` |
| f88 | (none) |
| first_digital_finance_corporation | (none) |
| khazna | (none) |
| koinworks | (none) |
| leasy | `currency_type` |
| lendmn | `currency_type` |
| lendmn_micro | `currency_type` |
| lendmn_revolving | `currency_type` |
| lhoopa | (none) |
| mkopa | (none) |
| moladin | (none) |
| moladin_property | (none) |
| mottu | (none) |
| mufin | (none) |
| payjoy | (none) |
| payjoy_ecuador | (none) |
| payjoy_full | (none) |
| payjoy_sa | (none) |
| payjoy_secured | (none) |
| r5 | (none) |
| samunnati | (none) |
| sary | (none) |
| shara | `currency_code` |
| solar_panda | (none) |
| sugmya_finance | `currency_type` |
| techcoop | (none) |
| validus_id | (none) |
| validus_id_full | (none) |
| validus_id_lendable | (none) |
| validus_id_secured | (none) |
| watu_africa | `currency_type` |

### `status`

| Borrower | Candidates |
|---|---|
| credismart | `loan_status` |
| dlight | (none) |
| exitus | `loan_status` |
| first_circle | (none) |
| first_digital_finance_corporation | `loan_status`, `loan_status_id` |
| koinworks | (none) |
| lhoopa | `construction_status`, `property_status` |
| metafin | `loan_status` |
| mkopa | (none) |
| moladin | `loan_status` |
| mottu | (none) |
| mufin | (none) |
| payjoy | (none) |
| payjoy_ecuador | `current_loan_status` |
| payjoy_full | (none) |
| payjoy_sa | `current_loan_status` |
| payjoy_secured | (none) |
| samunnati | (none) |
| sary | (none) |
| techcoop | (none) |
| validus_id | `repayment_status` |
| validus_id_full | `repayment_status` |
| validus_id_lendable | `repayment_status` |
| validus_id_secured | `repayment_status` |
| watu_africa | `asset_status`, `legal_status`, `loan_status` |

### `branch`

| Borrower | Candidates |
|---|---|
| amartha | `branch_id` |
| amartha_full | `branch_id` |
| amartha_full_portfolio | `branch_id` |
| amartha_secured | `branch_id` |
| autocheck__ci | (none) |
| autocheck__ug | (none) |
| credismart | (none) |
| dlight | (none) |
| exitus | (none) |
| first_circle | (none) |
| first_digital_finance_corporation | (none) |
| koinworks | (none) |
| lendmn | (none) |
| lendmn_micro | (none) |
| lendmn_revolving | (none) |
| lhoopa | (none) |
| metafin | (none) |
| mkopa | (none) |
| moladin | (none) |
| moladin_property | (none) |
| mottu | (none) |
| mufin | (none) |
| payjoy | (none) |
| payjoy_ecuador | (none) |
| payjoy_full | (none) |
| payjoy_sa | (none) |
| payjoy_secured | (none) |
| samunnati | (none) |
| sary | (none) |
| shara | (none) |
| solar_panda | (none) |
| sugmya_finance | (none) |
| techcoop | (none) |
| validus_id | (none) |
| validus_id_full | (none) |
| validus_id_lendable | (none) |
| validus_id_secured | (none) |
| watu_africa | (none) |

### `days_past_due`

| Borrower | Candidates |
|---|---|
| amartha | (none) |
| amartha_full | (none) |
| amartha_full_portfolio | (none) |
| amartha_secured | `principal_due_at_cutoff_loan` |
| credismart | `loan_due_date` |
| dlight | (none) |
| exitus | (none) |
| first_circle | (none) |
| koinworks | (none) |
| lendmn | (none) |
| lendmn_micro | (none) |
| lendmn_revolving | (none) |
| lhoopa | (none) |
| metafin | (none) |
| mkopa | (none) |
| mottu | (none) |
| mufin | `over_due_interest_amount_as_on_december_31_2025`, `over_due_principal_amount_as_on_december_31_2025` |
| payjoy | (none) |
| payjoy_full | (none) |
| payjoy_secured | (none) |
| samunnati | `dpd_days` |
| shara | (none) |
| solar_panda | `deposit_days` |
| sugmya_finance | (none) |
| techcoop | (none) |
| validus_id | (none) |
| validus_id_full | (none) |
| validus_id_lendable | `tenure_days` |
| validus_id_secured | `tenure_days` |
| watu_africa | (none) |

## Full column list per borrower

<details><summary>Expand — every column, every borrower</summary>

**advance**: `company_name`, `loan_id`, `customer_id`, `customer_birth_year`, `customer_gender`, `customer_sector`, `branch`, `status`, `product`, `purpose`, `begin_date`, `maturity_date`, `original_maturity_date`, `closure_date`, `company_due_date`, `principal_amount`, `total_loan_amount`, `interest_rate`, `interest_period`, `downpayment`, `fees`, `principal_remaining`, `principal_outstanding`, `interest_outstanding`, `fees_outstanding`, `penalties_outstanding`, `days_past_due`, `collateral_description`, `collateral_value`, `disburser`, `category`, `yearmonth`, `secured_loan`, `secured_loan_amount`, `data_filename`, `data_as_of_datetime`, `data_pipeline_run_as_of_datetime`, `pipeline_run_id`

**amartha**: `loan_id`, `customer_id`, `group_id`, `customer_gender`, `branch_id`, `product`, `customer_birth_year`, `loan_purpose`, `begin_date`, `maturity_date`, `interest_rate`, `interest_period`, `tenor`, `tenor_unit`, `dpd`, `expected_payment_amount_loan_per_installment`, `principal_amount_lendable`, `principal_amount_loan`, `principal_remaining_lendable`, `principal_remaining_loan`, `status`, `is_lendable`, `is_restructured`, `origin_loan_id`, `lender_group`, `data_filename`, `data_as_of_datetime`, `data_pipeline_run_as_of_datetime`, `pipeline_run_id`

**amartha2**: `idx`, `loan_id`, `customer_id`, `group_id`, `customer_gender`, `branch`, `product`, `customer_birth_year`, `loan_purpose`, `begin_date`, `maturity_date`, `interest_rate`, `interest_period`, `tenor`, `tenor_unit`, `days_past_due`, `expected_payment_amount_loan_per_installment`, `principal_amount_lendable`, `principal_amount_loan`, `principal_remaining_lendable`, `principal_remaining_loan`, `outstanding_amount_lendable`, `outstanding_amount_loan`, `principal_paid_loan`, `principal_due_at_cutoff_loan`, `status`, `is_cf`, `closure_date`

**amartha_full**: `cutoff_date`, `loan_id`, `customer_id`, `group_id`, `customer_gender`, `branch_id`, `product`, `customer_birth_year`, `loan_purpose`, `begin_date`, `maturity_date`, `interest_rate`, `interest_period`, `tenor`, `tenor_unit`, `dpd`, `expected_payment_amount_loan_per_installment`, `principal_amount_lendable`, `principal_amount_loan`, `principal_remaining_lendable`, `principal_remaining_loan`, `status`, `is_lendable`, `is_restructured`, `origin_loan_id`

**amartha_full_portfolio**: `cutoff_date`, `loan_id`, `customer_id`, `group_id`, `customer_gender`, `branch_id`, `product`, `customer_birth_year`, `loan_purpose`, `begin_date`, `maturity_date`, `interest_rate`, `interest_period`, `tenor`, `tenor_unit`, `dpd`, `expected_payment_amount_loan_per_installment`, `principal_amount_lendable`, `principal_amount_loan`, `principal_remaining_lendable`, `principal_remaining_loan`, `status`, `is_lendable`, `is_restructured`, `origin_loan_id`

**amartha_secured**: `loan_id`, `customer_id`, `group_id`, `customer_gender`, `branch_id`, `product`, `customer_birth_year`, `loan_purpose`, `begin_date`, `maturity_date`, `interest_rate`, `interest_period`, `tenor`, `tenor_unit`, `dpd`, `expected_payment_amount_loan_per_installment`, `principal_amount_lendable`, `principal_remaining_lendable`, `outstanding_amount_lendable`, `principal_amount_loan`, `principal_remaining_loan`, `outstanding_amount_loan`, `principal_paid_loan`, `principal_due_at_cutoff_loan`, `status`, `is_cf`, `closure_date`, `data_filename`, `data_as_of_datetime`, `data_pipeline_run_as_of_datetime`, `pipeline_run_id`

**autocheck__ci**: `loan_id`, `borrower_id`, `borrower_dob`, `borrower_gender`, `customer_sector`, `status`, `product`, `loan_purpose`, `begin_date`, `maturity_date`, `closure_date`, `loan_application_id`, `borrower_unique_number`, `principal_amount`, `interest_rate`, `interest_period`, `downpayment`, `principal_remaining`, `total_loan_amount`, `bcode`, `fees_out`, `interest_out`, `principal_out`, `penalty_out`, `days_past_due`, `collateral_id`, `collateral_description`, `collateral_value`, `fees`, `restructured_from`, `date_repossession`, `resale_value`, `date_resale`, `asset_mkt_value`, `ifrs_cat`, `discontinued_prod`

**autocheck__ug**: `loan_id`, `borrower_id`, `borrower_dob`, `borrower_gender`, `customer_sector`, `status`, `product`, `loan_purpose`, `begin_date`, `maturity_date`, `closure_date`, `loan_application_id`, `borrower_unique_number`, `principal_amount`, `interest_rate`, `interest_period`, `downpayment`, `principal_remaining`, `total_loan_amount`, `bcode`, `fees_out`, `interest_out`, `principal_out`, `penalty_out`, `days_past_due`, `collateral_id`, `collateral_description`, `collateral_value`, `fees`, `restructured_from`, `date_repossession`, `resale_value`, `date_resale`, `asset_mkt_value`, `ifrs_cat`, `discontinued_prod`

**credismart**: `product_name`, `unique_loan_identifier`, `unique_borrower_identifier`, `loan_disbursement_date`, `gross_monthly_interest_rate`, `loan_amount`, `expected_number_of_repayments`, `loan_due_date`, `total_principal_interest_and_other_charges_received_to_date`, `principal`, `interest`, `fees`, `other_fees_and_amounts`, `principal_still_outstanding`, `interest_still_outstanding`, `other_fees_and_charges_still_outstanding`, `loan_status`, `country_code`, `internal_score`, `term_unit`, `originator_agent`, `loan_use`, `restruture_to_loan_id`, `restruture_from_loan_id`, `schedule_installment_date`, `gender`, `year_of_birth`, `income_local_currency`, `estate`, `aditional_information`, `down_payment`, `lendable_secured`, `data_filename`, `data_as_of_datetime`, `data_pipeline_run_as_of_datetime`, `pipeline_run_id`

**dlight**: `contractid`, `customerid`, `contract_totalprice`, `contract_totaldeposit`, `contractregistrationdate_dt`, `end_date`, `product_plus_acessory`, `product_family`, `currency_type`, `country`, `contract_rateperunit`, `baseunit_ratetypeentity`, `contract_daysondeposit`, `term_length`, `accessory_deposit`, `upselldate`, `baseunit_contractstatus`

**exitus**: `loan_id`, `customer_id`, `principal`, `expected_total_interest`, `begin_date`, `end_date`, `product_type`, `principal_outstanding`, `currency_type`, `repossession_date`, `is_reschedule`, `is_refinanced`, `refinanced_loan_id`, `loan_status`, `payoff_date`, `default_date`, `upfront_fee`, `down_payment`, `interest_rate`, `interest_rate_period`, `interest_rate_type`

**f88**: `loan_id`, `customer_id`, `age_group`, `gender`, `branch`, `status`, `product`, `loan_purpose`, `begin_date`, `maturity_date`, `original_maturity_date`, `closure_date`, `principal_amount`, `interest_rate`, `interest_period`, `downpayment`, `fees`, `principal_remaining`, `principal_outstanding`, `interest_outstanding`, `fee_outstanding`, `penalty_outstanding`, `days_past_due`, `collateral_description`, `collateral_value`, `write_off_date`, `write_off_days`, `loan_type_tag`, `data_filename`, `data_as_of_datetime`, `data_pipeline_run_as_of_datetime`, `pipeline_run_id`

**first_circle**: `borrower_id`, `loan_id`, `product_type`, `credit_bureau`, `internal_score`, `loan_amount`, `currency`, `origination_fee`, `disbursement_date`, `effective_interest_rate`, `term`, `term_unit`, `loan_type`, `originator_agent`, `lender`, `restructure_from_loan_id`, `restructure_to_loan_id`, `updated_at_timestamp`, `data_filename`, `data_as_of_datetime`, `data_pipeline_run_as_of_datetime`, `pipeline_run_id`

**first_digital_finance_corporation**: `loan_id`, `client_id`, `office_id`, `office`, `original_office`, `product_id`, `product_name`, `principal`, `loan_status_id`, `loan_status`, `blacklist`, `client_total_loans`, `loan_number`, `loan_number_adjusted`, `paymentarrangement`, `monthly_nominal_interest_rate`, `annual_nominal_interest_rate`, `client_activation_date`, `disbursed_date`, `expected_maturity_date`, `first_due_date`, `due_already`, `closed_date`, `number_of_installments`, `principal_repaid`, `principal_outstanding`, `principal_overdue`, `interest_repaid`, `interest_outstanding`, `interest_overdue`, `fees_repaid`, `fees_outstanding`, `fees_overdue`, `penalties_outstanding`, `penalties_overdue`, `total_collected`, `last_payment_date`, `last_payment_amount`, `days_past_due`, `penalties_repaid`, `days_past_due_band`, `creditexperiment`, `payup_delinquency_reason`, `payup_attitude`, `rescheduled_count`, `last_reschedule_date`, `gender`, `facility`, `data_filename`, `data_as_of_datetime`, `data_pipeline_run_as_of_datetime`, `pipeline_run_id`

**khazna**: `loan_id`, `customer_id`, `birth_date`, `gender`, `customer_sector`, `status`, `product`, `loan_purpose`, `begin_date`, `maturity_date`, `closure_date`, `original_maturity_date`, `priciple_amount`, `total_loan_amount`, `intrest_rate`, `intrest_period`, `downpayment`, `fees`, `installment_count`, `principal_remaining`, `fees_remaining`, `principal_outstanding`, `interest_outstanding`, `fee_outstanding`, `days_past_due`, `collateral_description`, `collateral_value`, `paid_date`, `branch`, `company_name`, `use_khazna_card`, `debit_ref_number`, `credit_ref_number`, `employer_name`, `data_filename`, `data_as_of_datetime`, `data_pipeline_run_as_of_datetime`, `pipeline_run_id`

**koinworks**: `id`, `product_group`, `product_source`, `borrower_code`, `loan_code`, `disbursed_date`, `funding_amount`, `lender_rate`, `borrower_rate`, `is_interest_margin`, `tenure`, `is_npl_declared`, `is_top_up_loan`, `cleared_date`, `is_restructured`, `principal_remaining`, `interest_remaining`, `late_remaining`, `total_commission_fee`, `province`, `replicated_at`

**leasy**: `car_plate`, `loan_id`, `customer_id`, `customer_birth_year`, `customer_gender`, `customer_sector`, `branch`, `status`, `product`, `loan_purpose`, `begin_date`, `maturity_date`, `original_maturity_date`, `closure_date`, `principal_amount`, `total_loan_amount`, `interest_rate`, `interest_period`, `downpayment_usd`, `fees`, `principal_remaining`, `principal_outstanding`, `interest_outstanding`, `fee_outstanding`, `penalty_outstanding`, `days_past_due`, `collateral_description`, `collateral_value`, `purchase_price_usd`, `finance`, `data_filename`, `data_as_of_datetime`, `country`, `currency_type`, `downpayment`, `purchase_price`, `data_pipeline_run_as_of_datetime`, `pipeline_run_id`

**lendmn**: `loan_id`, `customer_id`, `principal`, `expected_total_interest`, `begin_date`, `end_date`, `product_type`, `principal_outstanding`, `currency_type`, `is_reschedule`, `is_refinanced`, `original_maturity_date`, `status`, `payoff_date`, `interest_rate`, `interest_rate_period`, `interest_rate_type`, `extended_loan_id`, `is_pledged`

**lendmn_micro**: `loan_id`, `customer_id`, `principal`, `expected_total_interest`, `begin_date`, `end_date`, `product_type`, `principal_outstanding`, `currency_type`, `is_reschedule`, `is_refinanced`, `original_maturity_date`, `status`, `payoff_date`, `interest_rate`, `interest_rate_period`, `interest_rate_type`, `extended_loan_id`, `is_pledged`, `loan_created_at`, `data_filename`, `data_as_of_datetime`, `data_pipeline_run_as_of_datetime`, `pipeline_run_id`

**lendmn_revolving**: `loan_id`, `customer_id`, `principal`, `expected_total_interest`, `begin_date`, `end_date`, `product_type`, `principal_outstanding`, `currency_type`, `is_reschedule`, `is_refinanced`, `original_maturity_date`, `status`, `payoff_date`, `interest_rate`, `interest_rate_period`, `interest_rate_type`, `extended_loan_id`, `is_pledged`, `is_converted_to_msme`, `data_filename`, `data_as_of_datetime`, `data_pipeline_run_as_of_datetime`, `pipeline_run_id`

**lhoopa**: `property_id`, `property_purchase_date`, `property_purchase_amount`, `acquisition_tax_amount`, `refurbishment_amount`, `actual_construction_amount_paid`, `predicted_sales_price`, `current_final_asking_price`, `approximate_location`, `purchase_method`, `purchase_type`, `tct_number`, `tct_registered_owner`, `property_status`, `construction_status`, `construction_progress`, `noa_date`, `submitted_to_escrow`, `is_approved`, `is_paid`, `is_pulled_out`, `is_relief`, `batch_no`, `submitted_date`, `pulled_out_date`, `data_filename`, `data_as_of_datetime`, `data_pipeline_run_as_of_datetime`, `pipeline_run_id`

**metafin**: `loan_id`, `unique_customer_id`, `dealer_id`, `principal_amount`, `asset_category`, `emi`, `tenure`, `emi_begin_date`, `emi_end_date`, `product_type`, `pos`, `currency`, `plant_size_kw`, `iot_installation_date`, `power_consumption`, `loan_status`, `date_restructured`, `revised_end_date`, `processing_fee`, `cost_of_acquired_assets`, `modules_used`, `roi`, `ltv`, `lendable_secured`, `data_filename`, `data_as_of_datetime`, `data_pipeline_run_as_of_datetime`, `pipeline_run_id`

**mkopa**: `customerid`, `customergender`, `buyertype`, `accountid`, `loanid`, `loansaledate`, `loanstartdate`, `loanenddate`, `loandurationindays`, `lendercategory`, `productcategory`, `productsubcategory`, `paymentplanname`, `repaymentmode`, `saleregion`, `salesubregion`, `saleshopname`

**moladin**: `loan_submission_id`, `active_loan_id`, `account_id`, `cost`, `amount`, `interest_rate`, `interest_period`, `interest_type`, `installment_type`, `loan_status`, `start_date`, `end_date`, `dpd0_date`, `origination_fee`, `asset`, `loan_purpose`, `days_past_due`, `principal_remaining`, `is_extended`, `is_refinanced`, `is_rolled_over`, `refinanced_rolled_over_loan_id`, `latest_extension_date`, `updated_end_date`, `asset_unique_identifier`, `external_asset_id`, `original_collateral_value_lcy`, `original_collateral_value_usd`, `corporate_counterparty`, `disbursement_channel`, `is_restructure`, `latest_restructure_date`, `name_of_pledgee`, `ever_pledged_to_lendable`, `extension_count`, `restructure_count`, `data_filename`, `data_as_of_datetime`, `file_name`, `data_pipeline_run_as_of_datetime`, `pipeline_run_id`

**moladin_property**: `loan_id`, `account_id`, `cost`, `amount__principal___interest___origination_fee_`, `interest_rate`, `interest_period`, `interest_type`, `installment_type`, `status`, `start_date`, `end_date`, `origination_fee__upfront_fee___discount_fee_`, `asset`, `loan_purpose`, `days_past_due`, `principal_remaining`, `is_restructured_`, `is_refinanced_`, `refinanced_rolled_over_loan_id`, `is_topup_without_close_`, `topup_without_close_id`, `loan_type`, `restructure_date`, `updated_end_date`, `asset_unique_identfier`, `external_asset_id`, `original_collateral_value_lcy`, `original_collateral_value_usd`, `corporate_counterparty`, `disbursement_channel`, `product_type`, `initial_deposit`, `deposit_refunded_at_close`, `remaining_deposit`

**mottu**: `contractid`, `userid`, `vehicleid`, `cicloid`, `producttype`, `transactionid`, `transactionnumber`, `rentalprice`, `amountpaid`, `begindate`, `enddate`, `paymentdate`, `duedate`, `contractsituation`, `lendable_secured`, `part`, `data_filename`, `data_as_of_datetime`, `data_pipeline_run_as_of_datetime`, `pipeline_run_id`

**mufin**: `s_no`, `loan_number`, `state`, `centre`, `gender`, `loan_date`, `month`, `loan_amount`, `collection_frequency`, `date_of_first_installment`, `date_of_last_installment`, `installment_amount`, `no_of_installments`, `last_receipt_date`, `dpd_as_on_december_31_2025`, `principal_outstanding_as_on_december_31_2025`, `over_due_principal_amount_as_on_december_31_2025`, `over_due_interest_amount_as_on_december_31_2025`, `principal_received`, `interest_received`, `aum_as_of_december_2025`, `product_type`, `customer_type`, `customer_sector`, `ltv`, `dealer_name`, `oem_name`, `date_of_repossession`, `dpd_on_date_of_repossesssion`, `principal_outstanding_on_date_of_repossession`, `dealer_subvention_used`, `impairment_recorded_on_repossession_through_bad_debt`, `impairment_recorded_on_repossession_through_interest_reversal`, `whether_sold_or_not`, `sold_till_quarter_end`, `sale_value`, `carring_value`, `principal_settled`, `interest_settled`, `remarks`, `processing_fees`, `gst`, `dealer_subvention`, `advance_emi`, `insurance_amount`, `stamp_duty`, `gps`, `dealer_upfront`, `insurance_ta`, `dealer_ta`, `refinance`, `disbursement_as_per_bank`, `security_deposit_received_battery`, `data_filename`, `data_as_of_datetime`, `data_pipeline_run_as_of_datetime`, `pipeline_run_id`

**payjoy**: `loansourceid`, `debitid`, `country`, `funder`, `origination_date`, `financeproduct`, `financestatus`, `months`, `financeamount`, `principal_outstanding`, `os_balance`, `last_payment`, `vintage`, `cumulative_payments`, `age`, `acurrent_to_2`, `a3_to_30`, `a31_to_60`, `a61_to_90`, `a91_to_120`, `a120_plus`, `amount_current_to_2`, `amount_3_to_30`, `amount_31_to_60`, `amount_61_to_90`, `amount_91_to_120`, `amount_120_plus`, `principal_current_to_2`, `principal_3_to_30`, `principal_31_to_60`, `principal_61_to_90`, `principal_91_to_120`, `principal_120_plus`, `data_filename`, `data_as_of_datetime`, `data_pipeline_run_as_of_datetime`, `pipeline_run_id`

**payjoy_ecuador**: `loan_product`, `loan_id`, `user_id`, `merchant_id`, `date`, `size_of_loan`, `principal_of_loan`, `interest_rate`, `loan_tenor_in_months`, `downpayment`, `current_loan_status`, `total_paid`, `total_no_paid`, `principal_outstanding`, `interest_outstanding`, `region`, `total_interest_to_be_paid`, `days_past_due`, `days_overdue`, `contract_assigned_to_lendable`, `expected_maturity_date`, `last_expected_payment_due_date`, `downpayment_perc`

**payjoy_full**: `loansourceid`, `debitid`, `country`, `funder`, `origination_date`, `financeproduct`, `financestatus`, `months`, `financeamount`, `principal_outstanding`, `os_balance`, `last_payment`, `vintage`, `cumulative_payments`, `age`, `acurrent_to_2`, `a3_to_30`, `a31_to_60`, `a61_to_90`, `a91_to_120`, `a120_plus`, `amount_current_to_2`, `amount_3_to_30`, `amount_31_to_60`, `amount_61_to_90`, `amount_91_to_120`, `amount_120_plus`, `principal_current_to_2`, `principal_3_to_30`, `principal_31_to_60`, `principal_61_to_90`, `principal_91_to_120`, `principal_120_plus`

**payjoy_sa**: `loan_product`, `loan_id`, `user_id`, `ownerentity`, `merchant_id`, `date`, `size_of_loan`, `principal_of_loan`, `interest_rate`, `loan_tenor_in_months`, `downpayment`, `current_loan_status`, `total_paid`, `total_no_paid`, `principal_outstanding`, `interest_outstanding`, `region`, `total_interest_to_be_paid`, `days_past_due`, `days_overdue`, `contract_assigned_to_lendable`, `expected_maturity_date`, `last_expected_payment_due_date`, `downpayment_perc`

**payjoy_secured**: `loansourceid`, `debitid`, `country`, `funder`, `origination_date`, `financeproduct`, `financestatus`, `months`, `financeamount`, `principal_outstanding`, `os_balance`, `last_payment`, `vintage`, `cumulative_payments`, `age`, `acurrent_to_2`, `a3_to_30`, `a31_to_60`, `a61_to_90`, `a91_to_120`, `a120_plus`, `amount_current_to_2`, `amount_3_to_30`, `amount_31_to_60`, `amount_61_to_90`, `amount_91_to_120`, `amount_120_plus`, `principal_current_to_2`, `principal_3_to_30`, `principal_31_to_60`, `principal_61_to_90`, `principal_91_to_120`, `principal_120_plus`, `data_filename`, `data_as_of_datetime`, `data_pipeline_run_as_of_datetime`, `pipeline_run_id`

**prestamype**: `loan_id`, `customer_id`, `customer_birth_year`, `customer_gender`, `customer_sector`, `branch`, `status`, `product`, `currency`, `asset_product`, `loan_purpose`, `begin_date`, `maturity_date`, `original_maturity_date`, `closure_date`, `principal_amount`, `total_loan_amount`, `interest_rate`, `interest_period`, `downpayment`, `fees`, `principal_remaining`, `principal_outstanding`, `interest_outstanding`, `fee_outstanding`, `penalty_outstanding`, `days_past_due`, `collateral_description`, `collateral_value`, `restructured_id`, `renewed_id`, `is_pledged_to_lendable`, `flag_lendable`, `pledged_to_lendable_date`, `data_filename`, `data_as_of_datetime`, `data_pipeline_run_as_of_datetime`, `pipeline_run_id`

**r5**: `loan_id`, `customer_id`, `customer_birth_year`, `customer_gender`, `customer_sector`, `branch`, `status`, `product`, `loan_purpose`, `begin_date`, `maturity_date`, `original_maturity_date`, `closure_date`, `principal_amount`, `principal_policy_amount`, `total_loan_amount`, `interest_rate`, `interest_period`, `downpayment`, `fees`, `origination_fee`, `commission_insurance`, `principal_remaining`, `principal_policy_remaining`, `principal_outstanding`, `interest_outstanding`, `fee_outstanding`, `penalty_outstanding`, `days_past_due`, `collateral_description`, `collateral_value`, `term`, `recovery_date`, `resale_date`, `resale_value`, `market_value`, `endorsed`

**samunnati**: `accountnumber`, `customerid`, `currentprincipal`, `totalinterestdue`, `processing_fee`, `disbursementdate`, `total_disbursed_amount`, `maturitydate`, `repaymenttype`, `producttype`, `collateral_type`, `value_chain`, `accountstatus`, `principaloutstanding`, `csa`, `csa_criteria`, `repossession_date`, `is_reschedule`, `is_refinanced`, `refinanced_loan_id`, `from_refi_flag`, `early_paid_off`, `early_paid_off_fee`, `writtenoffdate`, `writtenoffstatus`, `writeoff_amount`, `downpayment`, `interestrate`, `tenure`, `frequency`, `product_code`, `type_of_loan`, `netpay_off_amount`, `dpd_days`, `report_date`, `data_filename`, `data_as_of_datetime`, `data_pipeline_run_as_of_datetime`, `pipeline_run_id`

**sary**: `client_id_lh`, `client_establishment_type__business_individual_`, `client_city`, `loanid`, `loanstatus`, `start_date`, `due_date`, `tenure`, `invoice_amount`, `principalbalance`, `moratetaxincl`, `totalmargintaxincl`, `dayspastdue`, `product`, `amountpaid`, `lastpaid`, `credit_limit`

**shara**: `begin_date`, `currency_code`, `down_payment_amount`, `end_date`, `expected_total_interest`, `id`, `payoff_date`, `principal`, `principal_outstanding`, `product_type`, `status`, `updated_at`, `user_id`

**solar_panda**: `sale_id`, `customer_id`, `installed_date`, `package_name`, `status`, `price`, `deposit`, `daily_token_payment`, `monthly_token`, `contract_duration`, `deposit_days`, `end_date`, `product_group`

**solvento**: `loan_id`, `customer_id`, `customer_birth_year`, `customer_gender`, `customer_sector`, `branch`, `status`, `product`, `loan_purpose`, `begin_date`, `maturity_date`, `original_maturity_date`, `closure_date`, `principal_amount`, `total_loan_amount`, `interest_rate`, `interest_period`, `downpayment`, `fees`, `principal_remaining`, `principal_outstanding`, `interest_outstanding`, `fee_outstanding`, `penalty_outstanding`, `days_past_due`, `collateral_description`, `initial_collateral_value`, `current_collateral_value`, `repossession_date`, `liquidation_date`, `liquidation_amount`, `assigned_to`, `currency`, `lender_assignment_date`, `bundled_to`, `cep_url`, `is_restructured`, `federal_entity`, `credit_rating`, `max_days_past_due`, `tipo_de_persona`, `interest_accrued`, `interest_paid`, `data_filename`, `data_as_of_datetime`, `data_pipeline_run_as_of_datetime`, `pipeline_run_id`

**sugmya_finance**: `loan_id`, `customer_id`, `principal`, `expected_total_interest`, `begin_date`, `end_date`, `product_type`, `principal_outstanding`, `currency_type`, `repossession_date`, `is_reschedule`, `is_refinanced`, `refinanced_loan_id`, `from_refi_flag`, `status`, `payoff_date`, `default_date`, `upfront_fee`, `down_payment`, `interest_rate`, `interest_rate_period`, `interest_rate_type`, `gender`, `country`, `location`, `income_level`, `urban_type`, `first_time_borrower`, `principal_collected`, `pardays`, `closure_date`, `return_flag`, `row_num`

**techcoop**: `invoice_id`, `loan_id`, `customer_id`, `principal`, `expected_total_interest`, `begin_date`, `end_date`, `product_group`, `product`

**validus_id**: `loan_id`, `borrower_id`, `disbursement_date`, `maturity_date`, `interest_per_annum`, `partner_name`, `sector`, `repayment_status`, `repayment_date`, `product_type`, `total_loan`, `actual_interest_amount`, `dpd`, `os`, `funder_group`, `data_filename`, `data_as_of_datetime`, `data_pipeline_run_as_of_datetime`, `pipeline_run_id`

**validus_id_full**: `loan_id`, `borrower_id`, `disbursement_date`, `maturity_date`, `interest_per_annum`, `partner_name`, `sector`, `repayment_status`, `repayment_date`, `product_type`, `total_loan`, `actual_interest_amount`, `dpd`, `os`

**validus_id_lendable**: `borrower_id`, `loan_id`, `partner_name`, `disbursement_date`, `maturity_date`, `dpd`, `tenure_days`, `repayment_status`, `interest_per_annum`, `industry_group`, `funder_group`, `loan_amount`, `interest_amount`

**validus_id_secured**: `borrower_id`, `loan_id`, `partner_name`, `disbursement_date`, `maturity_date`, `dpd`, `tenure_days`, `repayment_status`, `interest_per_annum`, `industry_group`, `funder_group`, `loan_amount`, `interest_amount`, `data_filename`, `data_as_of_datetime`, `data_pipeline_run_as_of_datetime`, `pipeline_run_id`

**watu_africa**: `country`, `external_loan_id`, `external_account_id`, `begin_date`, `end_date`, `closure_date`, `principal`, `interest`, `fees`, `principal_outstanding`, `interest_outstanding`, `fee_outstanding`, `currency_type`, `loan_type`, `loan_status`, `waived_interest`, `waived_fees`, `interest_rate`, `interest_type`, `legal_status`, `repossession_date`, `initial_valuation`, `current_valuation`, `registration_no`, `asset_type`, `asset_status`, `export_date`, `data_filename`, `data_as_of_datetime`, `data_pipeline_run_as_of_datetime`, `pipeline_run_id`

</details>