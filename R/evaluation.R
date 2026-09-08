# =============================================================================
# Stage 8 -- evaluation of alert prioritisation policies
#
# Reference standard throughout: DDInter expert-assigned severity. This is a
# property of a knowledge base, not an observed patient outcome. "Major" here
# means "DDInter graded this pair Major", never "this patient was harmed".
# Every figure written by this script says so on its face.
#
# Leakage rule (eval/criteria.md section 3): severity is the label. Policy D's
# score was built from patient-context features only and never saw it. That is
# what makes the calibration and discrimination results below meaningful rather
# than circular.
#
# R is the honest tool for this stage rather than a language added for range:
# decision curve analysis is the framework that explicitly trades false
# positives against false negatives across a range of thresholds, which is
# literally the alert-fatigue problem.
# =============================================================================

suppressPackageStartupMessages({
  library(dplyr)
  library(tidyr)
  library(ggplot2)
})

set.seed(42)

# -----------------------------------------------------------------------------
# R IDIOM NOTES (this project is meant to be explainable, so the idioms are
# spelled out where they first appear)
#
# |>            the native pipe. `x |> f(y)` is exactly `f(x, y)`. It reads
#               left-to-right so a chain of verbs reads as a sentence.
# mutate()      add or overwrite columns, vectorised over the whole frame.
# summarise()   collapse rows to one row per group.
# group_by()    declares the grouping that summarise() collapses over.
# .by =         a newer inline alternative to group_by() for a single verb.
# factor        R's categorical type. Levels control ordering in tables and in
#               ggplot legends -- set them deliberately or you get alphabetical.
# ~             the formula interface. `y ~ x` is a *specification*, not a
#               computation: it names an outcome and predictors and hands them
#               to a modelling function to interpret.
# -----------------------------------------------------------------------------

OUT <- "outputs"
alerts <- read.csv(file.path(OUT, "alerts_for_r.csv"), stringsAsFactors = FALSE)

# Severity as an ordered factor. Ordering matters: without it ggplot and table()
# sort alphabetically and "Major" lands between "Minor" and "Moderate", which is
# both ugly and easy to misread.
alerts$severity <- factor(alerts$severity,
                          levels = c("Major", "Moderate", "Unknown", "Minor"))

N_ALERTS   <- nrow(alerts)
N_PATIENTS <- length(unique(alerts$patient_id))
PREV       <- mean(alerts$is_major)

cat(sprintf(
  "Loaded %s alerts from %s patients. Major prevalence %.4f (%s alerts).\n",
  format(N_ALERTS, big.mark = ","), format(N_PATIENTS, big.mark = ","),
  PREV, format(sum(alerts$is_major), big.mark = ",")))

CAPTION <- paste0(
  "Reference standard: DDInter expert-assigned severity (a knowledge-base ",
  "property, not an observed outcome).\n",
  "n = ", format(N_ALERTS, big.mark = ","), " alerts from ",
  format(N_PATIENTS, big.mark = ","), " patients, 2021-09-07 to 2026-09-07. ",
  "Synthetic prescribing data (Synthea, seed 42).")

# =============================================================================
# 1. DISCRIMINATION -- does Policy D's score separate Major from non-Major?
#
# AUC is computed from ranks rather than by integrating an ROC curve. For a
# binary outcome the Mann-Whitney identity gives it exactly:
#
#     AUC = (mean rank of positives - (n_pos + 1) / 2) / n_neg
#
# which is the probability that a randomly chosen Major alert scores above a
# randomly chosen non-Major one. Writing it this way makes the interpretation
# visible; calling a package's auc() hides it.
# =============================================================================

auc_from_ranks <- function(score, label) {
  r <- rank(score)
  n_pos <- sum(label == 1)
  n_neg <- sum(label == 0)
  (mean(r[label == 1]) - (n_pos + 1) / 2) / n_neg
}

auc_d <- auc_from_ranks(alerts$score_D_scaled, alerts$is_major)
cat(sprintf("\nPolicy D score AUC vs Major: %.4f  (0.5 = no discrimination)\n", auc_d))

# The formula interface. `is_major ~ score_D_scaled` specifies the model; glm()
# reads that specification. family = binomial() makes it logistic regression.
# The coefficient answers "does the score move the log-odds of Major at all?"
fit <- glm(is_major ~ score_D_scaled, family = binomial(), data = alerts)
co <- summary(fit)$coefficients
cat(sprintf("Logistic slope on score: %.3f (SE %.3f, p = %.3g)\n",
            co[2, 1], co[2, 2], co[2, 4]))
cat("  NOTE: this p-value treats 36,929 alerts as independent. They are not --\n")
cat("  they come from 524 patients. It is reported only to show the effect is\n")
cat("  small; the clustered bootstrap below is the inference that counts.\n")

# =============================================================================
# 2. CALIBRATION against the stated reference standard
#
# Decile bins rather than a loess smoother. With a near-flat relationship a
# smoother invites the reader to see structure in noise; binned points with
# intervals show flatness for what it is.
#
# Wilson intervals rather than normal-approximation (Wald) intervals. Wald
# breaks down badly for proportions near 0, and the Major rate here is ~5%, so
# several bins would otherwise get intervals crossing zero.
# =============================================================================

wilson_ci <- function(k, n, z = 1.96) {
  p <- k / n
  denom  <- 1 + z^2 / n
  centre <- (p + z^2 / (2 * n)) / denom
  halfw  <- z * sqrt(p * (1 - p) / n + z^2 / (4 * n^2)) / denom
  c(lower = max(0, centre - halfw), upper = min(1, centre + halfw))
}

calib <- alerts |>
  mutate(bin = ntile(score_D_scaled, 10)) |>
  summarise(
    n            = n(),
    mean_score   = mean(score_D_scaled),
    observed     = mean(is_major),
    k            = sum(is_major),
    .by = bin
  ) |>
  rowwise() |>
  mutate(lower = wilson_ci(k, n)[["lower"]],
         upper = wilson_ci(k, n)[["upper"]]) |>
  ungroup() |>
  arrange(bin)   # .by = does not sort; without this the bins print scrambled

print(as.data.frame(calib), row.names = FALSE)
write.csv(calib, file.path(OUT, "r_calibration_bins.csv"), row.names = FALSE)

# ggplot layering: start with the data and aesthetic mapping, then add geoms.
# Each `+` adds a layer drawn on top of the last.
p_cal <- ggplot(calib, aes(x = mean_score, y = observed)) +
  geom_hline(yintercept = PREV, linetype = "dashed", colour = "#b91c1c") +
  annotate("text", x = max(calib$mean_score), y = PREV,
           label = sprintf("  overall Major rate %.1f%%", 100 * PREV),
           hjust = 1, vjust = -0.8, size = 3, colour = "#b91c1c") +
  geom_errorbar(aes(ymin = lower, ymax = upper), width = 0.012,
                colour = "#15803d", linewidth = 0.5) +
  geom_point(size = 2.4, colour = "#15803d") +
  scale_y_continuous(labels = scales::percent, limits = c(0, NA)) +
  labs(
    title    = "Policy D score is not calibrated to interaction severity",
    subtitle = paste("Observed proportion of Major alerts by decile of the",
                     "patient-context score.\nA useful score would rise left to",
                     "right. This one rises then falls, and the top-scoring",
                     "decile\nsits below the overall Major rate."),
    x = "Mean Policy D context score (rescaled 0-1)",
    y = "Observed proportion Major",
    caption = CAPTION
  ) +
  theme_minimal(base_size = 11) +
  theme(plot.caption = element_text(size = 7, colour = "grey35", hjust = 0))

ggsave(file.path(OUT, "r_calibration.png"), p_cal,
       width = 8, height = 5.6, dpi = 200)

# =============================================================================
# 3. DECISION CURVE ANALYSIS / NET BENEFIT
#
#     NB(p_t) = TP/n - (FP/n) * (p_t / (1 - p_t))
#
# The "treatment" is INTERRUPTING THE PRESCRIBER.
#   TP  an interruption raised on a Major alert
#   FP  an interruption raised on anything else
#   p_t the minimum probability of Major at which being interrupted is worth it
#
# The odds term p_t/(1-p_t) is the exchange rate between the two errors. At
# p_t = 0.05 it equals 1/19: the analyst is stating that one missed Major alert
# is worth nineteen needless interruptions. That single term is the whole
# argument for DCA over accuracy or F1, which fix an exchange rate silently.
#
# Threshold range 1%-30%. Major prevalence is 5.25%; past roughly 20% the
# analyst is claiming a willingness-to-be-interrupted above the base rate, and
# every strategy falls below "interrupt none".
# =============================================================================

net_benefit <- function(interrupt, is_major, p_t, n = length(is_major)) {
  tp <- sum(interrupt == 1 & is_major == 1)
  fp <- sum(interrupt == 1 & is_major == 0)
  tp / n - (fp / n) * (p_t / (1 - p_t))
}

thresholds <- seq(0.01, 0.30, by = 0.005)

# Fixed-classification strategies: the policies as actually specified, at the
# pre-registered 5-per-patient-day budget, plus the two reference strategies.
strategies <- list(
  "Interrupt all (Policy A)"       = alerts$interrupt_A_unlim,
  "Policy A, budget 5"             = alerts$interrupt_A_5,
  "Policy B, severity (budget 5)"  = alerts$interrupt_B_5,
  "Policy D, context (budget 5)"   = alerts$interrupt_D_5
)

dca_fixed <- lapply(names(strategies), function(nm) {
  data.frame(
    strategy  = nm,
    threshold = thresholds,
    nb = vapply(thresholds,
                function(p) net_benefit(strategies[[nm]], alerts$is_major, p),
                numeric(1))
  )
}) |> bind_rows()

# "Interrupt none" is net benefit 0 by construction: no true positives gained,
# no false positives paid for.
dca_none <- data.frame(strategy = "Interrupt none", threshold = thresholds, nb = 0)

# Policy D swept as a score: interrupt when the rescaled score exceeds the
# threshold. This is the standard model curve in DCA, and it is exactly the
# step that assumes the score behaves like a probability -- which section 2
# has just shown it does not. Included so the consequence of that assumption is
# visible rather than asserted.
dca_score <- data.frame(
  strategy  = "Policy D score swept as if a probability",
  threshold = thresholds,
  nb = vapply(thresholds, function(p)
    net_benefit(as.integer(alerts$score_D_scaled >= p), alerts$is_major, p),
    numeric(1))
)

dca <- bind_rows(dca_fixed, dca_none, dca_score)
write.csv(dca, file.path(OUT, "r_decision_curve.csv"), row.names = FALSE)

p_dca <- ggplot(dca, aes(threshold, nb, colour = strategy, linetype = strategy)) +
  geom_line(linewidth = 0.9) +
  geom_vline(xintercept = PREV, linetype = "dotted", colour = "grey40") +
  annotate("text", x = PREV, y = max(dca$nb), hjust = -0.05, size = 3,
           colour = "grey35",
           label = sprintf("Major prevalence %.1f%%", 100 * PREV)) +
  scale_x_continuous(labels = scales::percent) +
  labs(
    title    = "Decision curve: net benefit of interrupting a prescriber",
    subtitle = paste0("Threshold probability is the minimum chance of a Major ",
                      "interaction that justifies an interruption.\nAt 5% the ",
                      "analyst is trading one missed Major for nineteen ",
                      "needless interruptions."),
    x = "Threshold probability of a Major interaction",
    y = "Net benefit",
    colour = NULL, linetype = NULL,
    caption = CAPTION
  ) +
  theme_minimal(base_size = 11) +
  theme(legend.position = "bottom",
        legend.text = element_text(size = 8),
        plot.caption = element_text(size = 7, colour = "grey35", hjust = 0)) +
  guides(colour = guide_legend(nrow = 3), linetype = guide_legend(nrow = 3))

ggsave(file.path(OUT, "r_decision_curve.png"), p_dca,
       width = 8.5, height = 6.2, dpi = 200)

# =============================================================================
# 4. CLUSTERED BOOTSTRAP -- the inference that counts
#
# 36,929 alerts come from 524 patients, and one patient contributes up to 350.
# Alerts within a patient are strongly dependent: same drugs, same comorbidity,
# same repeated pair. Resampling alerts would treat 350 re-notifications of one
# warning as 350 independent facts and produce intervals far too narrow.
#
# So resample PATIENTS with replacement, carrying all of each sampled patient's
# alerts. This is the standard cluster bootstrap and it is the difference
# between an interval you can defend and one you cannot.
# =============================================================================

B <- 400
patients <- unique(alerts$patient_id)
by_patient <- split(seq_len(N_ALERTS), alerts$patient_id)

boot_stat <- function(idx) {
  d <- alerts[idx, ]
  c(
    auc      = auc_from_ranks(d$score_D_scaled, d$is_major),
    nb_B     = net_benefit(d$interrupt_B_5, d$is_major, 0.05, nrow(d)),
    nb_D     = net_benefit(d$interrupt_D_5, d$is_major, 0.05, nrow(d)),
    nb_all   = net_benefit(d$interrupt_A_unlim, d$is_major, 0.05, nrow(d)),
    major_B  = sum(d$interrupt_B_5 == 1 & d$is_major == 1) / max(1, sum(d$is_major)),
    major_D  = sum(d$interrupt_D_5 == 1 & d$is_major == 1) / max(1, sum(d$is_major))
  )
}

boot <- replicate(B, {
  drawn <- sample(patients, length(patients), replace = TRUE)
  boot_stat(unlist(by_patient[drawn], use.names = FALSE))
})

boot_ci <- apply(boot, 1, quantile, probs = c(0.025, 0.5, 0.975), na.rm = TRUE)
cat("\nCluster bootstrap over patients (B = ", B, "), 95% percentile intervals:\n",
    sep = "")
print(round(t(boot_ci), 4))
write.csv(as.data.frame(t(boot_ci)) |> tibble::rownames_to_column("statistic"),
          file.path(OUT, "r_bootstrap_ci.csv"), row.names = FALSE)

# =============================================================================
# 5. SUBGROUP PERFORMANCE
#
# The three subgroups were fixed in eval/criteria.md section 11 before any
# results were seen, precisely so that this section cannot become a search for
# a cut where Policy D happens to win.
# =============================================================================

subgroups <- list(
  "All alerts"                  = rep(TRUE, N_ALERTS),
  "Older adults (65+)"          = alerts$sg_older == 1,
  "High polypharmacy (5+ ing.)" = alerts$sg_polypharmacy == 1,
  "Renal-relevant condition"    = alerts$sg_renal == 1
)

sub_tbl <- lapply(names(subgroups), function(nm) {
  d <- alerts[subgroups[[nm]], ]
  maj <- sum(d$is_major)
  data.frame(
    subgroup      = nm,
    alerts        = nrow(d),
    patients      = length(unique(d$patient_id)),
    major_alerts  = maj,
    major_prev    = round(mean(d$is_major), 4),
    auc_D         = round(auc_from_ranks(d$score_D_scaled, d$is_major), 4),
    burden_B      = sum(d$interrupt_B_5),
    major_kept_B  = round(100 * sum(d$interrupt_B_5 == 1 & d$is_major == 1) / maj, 1),
    burden_D      = sum(d$interrupt_D_5),
    major_kept_D  = round(100 * sum(d$interrupt_D_5 == 1 & d$is_major == 1) / maj, 1)
  )
}) |> bind_rows()

cat("\nSubgroup performance at the 5-per-patient-day budget:\n")
print(sub_tbl, row.names = FALSE)
write.csv(sub_tbl, file.path(OUT, "r_subgroups.csv"), row.names = FALSE)

p_sub <- sub_tbl |>
  select(subgroup, B = major_kept_B, D = major_kept_D) |>
  pivot_longer(c(B, D), names_to = "policy", values_to = "major_kept") |>
  mutate(subgroup = factor(subgroup, levels = rev(sub_tbl$subgroup))) |>
  ggplot(aes(major_kept, subgroup, fill = policy)) +
  geom_col(position = position_dodge(width = 0.7), width = 0.62) +
  geom_text(aes(label = sprintf("%.0f%%", major_kept)),
            position = position_dodge(width = 0.7), hjust = -0.15, size = 3) +
  scale_fill_manual(values = c(B = "#1b6ca8", D = "#15803d"),
                    labels = c(B = "B - severity only", D = "D - context-aware")) +
  scale_x_continuous(limits = c(0, 108), labels = function(x) paste0(x, "%")) +
  labs(title = "Major alerts retained at a 5-per-patient-day budget",
       subtitle = paste("Subgroups fixed in eval/criteria.md before results",
                        "were seen.\nPolicy B leads in every subgroup."),
       x = "% of Major alerts still interrupting", y = NULL, fill = NULL,
       caption = CAPTION) +
  theme_minimal(base_size = 11) +
  theme(legend.position = "bottom",
        plot.caption = element_text(size = 7, colour = "grey35", hjust = 0))

ggsave(file.path(OUT, "r_subgroups.png"), p_sub, width = 8.5, height = 5, dpi = 200)

cat("\nWrote: r_calibration.png, r_decision_curve.png, r_subgroups.png,\n")
cat("       r_calibration_bins.csv, r_decision_curve.csv, r_bootstrap_ci.csv,\n")
cat("       r_subgroups.csv\n")

# =============================================================================
# WHAT NET BENEFIT MEANS HERE, AND WHAT IT DOES NOT
#
# WHAT IT MEANS.
# Net benefit puts burden and safety in one number by making the exchange rate
# between them explicit. At threshold p_t, one true positive is worth
# (1 - p_t)/p_t false positives, and the analyst must state p_t rather than let
# a metric choose it invisibly. A strategy is worth considering only where its
# curve sits above both "interrupt none" and "interrupt all". That framing
# matches the alert-fatigue problem exactly: the question was never "is this
# alert correct?" but "is interrupting a prescriber worth it at this rate?".
#
# WHAT IT DOES NOT MEAN.
#
#   POLICY B'S DOMINANCE IS PARTLY DEFINITIONAL, AND THE CURVE MUST BE READ
#   WITH THAT IN MIND. Policy B's rule is "interrupt if and only if severity is
#   Major". The outcome is "severity is Major". So B has zero false positives by
#   construction, which is why its curve is flat and positive across the whole
#   threshold range -- a flat line is the signature of a strategy with no false
#   positives to be penalised for. B is not discovering anything; it is being
#   scored against the very quantity it is defined on.
#
#   That does not make the comparison vacuous, but it does relocate where the
#   information is. The informative content is among the LABEL-BLIND
#   strategies: Policy D and the budgeted Policy A never see severity, and D --
#   which was built to be clever -- fails to beat A, which is arbitrary
#   selection. The honest claim is therefore not "B is excellent" but "nothing
#   that avoids using severity manages to approximate it", which is the same
#   conclusion Stage 7 reached from a different direction.
#
#   It does not demonstrate reduced adverse drug events. The outcome here is
#   "DDInter graded this pair Major", a property of a knowledge base. No
#   patient harm was observed in this data because none is recorded in it. A
#   policy scoring well has retained alerts an expert panel considers serious;
#   it has not prevented anything.
#
#   The "true positive" is a proxy, and a weak one. Some Major-graded pairs are
#   irrelevant for a given patient at a given dose, and some pairs graded
#   Moderate or Unknown are dangerous for a specific patient. The published
#   finding that over half of clinician overrides are inappropriate, at roughly
#   six times the adverse event rate, is precisely a finding that neither the
#   alerting nor the overriding is reliable. Net benefit computed against one
#   of those unreliable standards inherits its errors.
#
#   The threshold has no empirical anchor here. In a diagnostic setting p_t is
#   elicited from clinicians or from decision-analytic modelling. Nobody has
#   told us the rate at which an interruption is worth a missed Major
#   interaction. The curves show the consequences across a range; they do not
#   identify the right point on it.
#
#   The 18% of prescriptions whose drugs are absent from DDInter are invisible
#   to every curve here. They cannot generate an alert, so they cannot be a
#   true or a false positive, and the denominators exclude them silently.
#
#   Confidence intervals are clustered by patient, which handles dependence but
#   not the deeper problem: the cohort is synthetic, generated to clinical
#   guidelines, and tidier than real prescribing. The intervals describe
#   sampling variability within Synthea, not uncertainty about a real hospital.
# =============================================================================
