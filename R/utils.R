# =============================================================================
# Dataset-agnostic helpers used by R/evaluation.R. Nothing here reads the
# alert data; tests live in tests/testthat/test-utils.R.
# =============================================================================

#' Area under the ROC curve, computed from ranks
#'
#' @details
#' From ranks rather than a package call, so the reported quantity stays explicit.
#' This is the Mann-Whitney form: the probability that a randomly chosen
#' positive scores above a randomly chosen negative, with ties counting half
#' (`rank()` assigns tied scores their average rank).
#'
#' @param score Numeric vector of scores; higher means more likely positive.
#' @param label Vector of 0/1 outcomes, the same length as `score`. Values
#'   other than 0 and 1 are counted in neither class but still take a rank.
#' @return A single numeric in [0, 1]: 1 for a perfect separator, 0 for a
#'   perfectly reversed one, 0.5 for no discrimination. `NaN` when either class
#'   is empty.
#' @examples
#' auc_from_ranks(score = c(0.1, 0.4, 0.35, 0.8), label = c(0, 0, 1, 1))
#' # 0.75
auc_from_ranks <- function(score, label) {
  r <- rank(score)
  n_pos <- sum(label == 1)
  n_neg <- sum(label == 0)
  (mean(r[label == 1]) - (n_pos + 1) / 2) / n_neg
}

#' Wilson score confidence interval for a single proportion
#'
#' @details
#' Wilson intervals are more stable than Wald at low Major prevalence.
#' At k = 0 or k = n the Wald interval collapses to zero width; the Wilson
#' interval does not. Limits are clipped so they never leave [0, 1]; the clip
#' does not round inward, so at k = n the upper limit can be 1 - 1e-16.
#'
#' @param k Number of successes, a single non-negative count.
#' @param n Number of trials, a single positive count with `k <= n`.
#' @param z Standard normal quantile for the coverage wanted; the default 1.96
#'   gives a two-sided 95% interval.
#' @return A named numeric vector of length 2, `c(lower = , upper = )`, with
#'   `0 <= lower <= k/n <= upper <= 1` up to floating-point rounding.
#' @examples
#' wilson_ci(7, 40)
#' # lower 0.0875, upper 0.3195
#' wilson_ci(0, 20)
#' # lower 0, upper 0.1611 -- Wald would give 0 to 0
wilson_ci <- function(k, n, z = 1.96) {
  p <- k / n
  denom  <- 1 + z^2 / n
  centre <- (p + z^2 / (2 * n)) / denom
  halfw  <- z * sqrt(p * (1 - p) / n + z^2 / (4 * n^2)) / denom
  c(lower = max(0, centre - halfw), upper = min(1, centre + halfw))
}

#' Net benefit of a fixed interrupt/don't-interrupt classification
#'
#' @details
#' NB(p_t) = TP/n - (FP/n) * (p_t / (1 - p_t)). The odds term is the exchange
#' rate between the two errors: at p_t = 0.05 one true positive is worth
#' nineteen false positives. "Interrupt none" scores 0 by construction.
#'
#' @param interrupt Vector of 0/1 decisions, 1 = interrupt the prescriber.
#' @param is_major Vector of 0/1 outcomes, the same length as `interrupt`.
#' @param p_t Threshold probability in (0, 1).
#' @param n Denominator; defaults to `length(is_major)`.
#' @return A single numeric, in units of true positives per alert. Positive
#'   means better than interrupting no one at this threshold. If `p_t` is a
#'   vector, one value per threshold.
#' @examples
#' net_benefit(interrupt = c(1, 1, 0, 0), is_major = c(1, 0, 1, 0), p_t = 0.2)
#' # 1/4 - (1/4) * (0.2 / 0.8) = 0.1875
net_benefit <- function(interrupt, is_major, p_t, n = length(is_major)) {
  tp <- sum(interrupt == 1 & is_major == 1)
  fp <- sum(interrupt == 1 & is_major == 0)
  tp / n - (fp / n) * (p_t / (1 - p_t))
}
