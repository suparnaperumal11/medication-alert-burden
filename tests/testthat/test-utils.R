# Run from the repo root: Rscript -e 'testthat::test_dir("tests/testthat")'
# test_dir() sets the working directory to this folder, hence ../../

source("../../R/utils.R")

# auc_from_ranks ---------------------------------------------------------------

test_that("auc_from_ranks is 1 for a perfect separator", {
  expect_equal(auc_from_ranks(c(0.1, 0.2, 0.8, 0.9), c(0, 0, 1, 1)), 1)
})

test_that("auc_from_ranks is 0 for a perfectly reversed separator", {
  expect_equal(auc_from_ranks(c(0.9, 0.8, 0.2, 0.1), c(0, 0, 1, 1)), 0)
})

test_that("auc_from_ranks is 0.5 when every score is tied", {
  expect_equal(auc_from_ranks(rep(0.3, 6), c(0, 1, 0, 1, 0, 1)), 0.5)
})

test_that("auc_from_ranks matches a hand-computed example with a tie", {
  # Positives 0.5, 0.9; negatives 0.1, 0.2, 0.5. Over the 2 x 3 = 6 pairs:
  #   0.5 beats 0.1 and 0.2, ties 0.5     -> 1 + 1 + 0.5 = 2.5
  #   0.9 beats all three                 -> 3
  # AUC = 5.5 / 6 = 11/12 = 0.91667
  # Via ranks: 0.1->1, 0.2->2, 0.5,0.5->3.5, 0.9->5; positive mean 4.25;
  #   (4.25 - (2 + 1)/2) / 3 = 2.75 / 3 = 11/12
  score <- c(0.2, 0.5, 0.5, 0.9, 0.1)
  label <- c(0,   1,   0,   1,   0)
  expect_equal(auc_from_ranks(score, label), 11 / 12)
})

test_that("auc_from_ranks agrees with pROC::auc on random data with ties", {
  skip_if_not_installed("pROC")
  set.seed(2026)
  label <- rbinom(500, 1, 0.1)
  score <- round(rnorm(500) + 0.7 * label, 1)   # rounding forces ties
  ref <- pROC::auc(pROC::roc(label, score, levels = c(0, 1),
                             direction = "<", quiet = TRUE))
  expect_equal(auc_from_ranks(score, label), as.numeric(ref), tolerance = 1e-12)
})

# wilson_ci --------------------------------------------------------------------

test_that("wilson_ci agrees with prop.test's score interval", {
  # The formula is the score interval without continuity correction, method 3
  # in Newcombe RG, Stat Med 1998;17(8):857-72 (PMID 9595616). That paper is
  # cited for the formula only; no expected value here is taken from it.
  # The reference values come from base R, an independent implementation.
  # prop.test uses z = qnorm(0.975) = 1.959964, not 1.96, so pass it through.
  z <- qnorm(0.975)
  for (n in c(1, 2, 3, 7, 20, 29, 148, 263, 1000)) {
    for (k in unique(pmin(c(0, 1, 2, n %/% 3, n %/% 2, n - 1, n), n))) {
      # The chi-squared warning at small n concerns the test, not the interval
      ref <- suppressWarnings(prop.test(k, n, correct = FALSE))$conf.int
      expect_equal(unname(wilson_ci(k, n, z = z)), as.numeric(ref),
                   tolerance = 1e-12, label = sprintf("wilson_ci(%d, %d)", k, n))
    }
  }
})

test_that("wilson_ci does not collapse at k = 0, where Wald gives [0, 0]", {
  # Closed form at k = 0: lower 0, upper z^2 / (n + z^2)
  ci <- wilson_ci(0, 40)
  expect_equal(ci[["lower"]], 0)
  expect_equal(ci[["upper"]], 1.96^2 / (40 + 1.96^2))
  expect_gt(ci[["upper"]], 0)
})

test_that("wilson_ci does not collapse at k = n, where Wald gives [1, 1]", {
  # Closed form at k = n: lower n / (n + z^2), upper 1
  ci <- wilson_ci(40, 40)
  expect_equal(ci[["lower"]], 40 / (40 + 1.96^2))
  expect_equal(ci[["upper"]], 1)
  expect_lt(ci[["lower"]], 1)
})

test_that("wilson_ci stays inside [0, 1] and brackets k/n", {
  # At k = 0 and k = n the inner limit can miss k/n by one rounding step
  # (upper = 1 - 1.1e-16 at n = 100, k = 100), hence the 1e-12 allowance.
  for (n in c(1, 2, 5, 17, 100)) {
    for (k in 0:n) {
      ci <- wilson_ci(k, n)
      expect_gte(ci[["lower"]], 0)
      expect_lte(ci[["upper"]], 1)
      expect_lte(ci[["lower"]], k / n + 1e-12)
      expect_gte(ci[["upper"]], k / n - 1e-12)
    }
  }
})

test_that("wilson_ci is asymmetric about p near a boundary", {
  # p = 1/50 = 0.02: the interval must stretch further toward the middle
  ci <- wilson_ci(1, 50)
  p <- 1 / 50
  expect_gt(ci[["upper"]] - p, 2 * (p - ci[["lower"]]))
})

test_that("wilson_ci is mirror-symmetric under k -> n - k", {
  ci  <- wilson_ci(3, 25)
  mir <- wilson_ci(22, 25)
  expect_equal(mir[["lower"]], 1 - ci[["upper"]])
  expect_equal(mir[["upper"]], 1 - ci[["lower"]])
})

# net_benefit ------------------------------------------------------------------

is_major <- c(1, 1, 0, 0, 0, 0, 0, 0)   # prevalence 2/8 = 0.25
thresholds <- seq(0.01, 0.30, by = 0.005)

test_that("net_benefit of interrupting none is exactly 0 at every threshold", {
  for (p in thresholds) {
    expect_identical(net_benefit(rep(0, 8), is_major, p), 0)
  }
})

test_that("net_benefit of interrupting all is 0 at threshold = prevalence", {
  # 2/8 - (6/8) * (0.25 / 0.75) = 0.25 - 0.25 = 0
  expect_equal(net_benefit(rep(1, 8), is_major, 0.25), 0)
  # and positive below prevalence, negative above it
  expect_gt(net_benefit(rep(1, 8), is_major, 0.20), 0)
  expect_lt(net_benefit(rep(1, 8), is_major, 0.30), 0)
})

test_that("net_benefit with no false positives is tp/n at every threshold", {
  interrupt <- c(1, 0, 0, 0, 0, 0, 0, 0)   # one Major caught, nothing else
  for (p in thresholds) {
    expect_identical(net_benefit(interrupt, is_major, p), 1 / 8)
  }
})

test_that("net_benefit matches a hand-computed example", {
  # n = 10, 3 Major. Interrupt alerts 1, 2, 4, 5:
  #   TP = 2 (alerts 1, 2), FP = 2 (alerts 4, 5)
  # At p_t = 0.2, odds = 0.2 / 0.8 = 0.25:
  #   NB = 2/10 - (2/10) * 0.25 = 0.20 - 0.05 = 0.15
  major     <- c(1, 1, 1, 0, 0, 0, 0, 0, 0, 0)
  interrupt <- c(1, 1, 0, 1, 1, 0, 0, 0, 0, 0)
  expect_equal(net_benefit(interrupt, major, 0.2), 0.15)
})

test_that("net_benefit uses an explicit n in place of the vector length", {
  # Same TP = 2, FP = 2 as above, but n = 20: 0.10 - 0.10 * 0.25 = 0.075
  major     <- c(1, 1, 1, 0, 0, 0, 0, 0, 0, 0)
  interrupt <- c(1, 1, 0, 1, 1, 0, 0, 0, 0, 0)
  expect_equal(net_benefit(interrupt, major, 0.2, n = 20), 0.075)
})
