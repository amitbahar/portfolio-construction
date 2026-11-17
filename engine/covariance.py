"""Ledoit-Wolf shrinkage covariance in plain numpy (matches sklearn.covariance.LedoitWolf).

Reimplemented so the browser build does not need to download scikit-learn.
"""
import numpy as np


def ledoit_wolf(X):
    """Shrink the sample covariance of X (rows = observations) toward a scaled identity."""
    X = np.asarray(X, float)
    n, p = X.shape
    X = X - X.mean(axis=0)
    emp_cov = X.T @ X / n
    X2 = X ** 2
    emp_cov_trace = X2.sum(axis=0) / n
    mu = emp_cov_trace.sum() / p
    beta_ = (X2.T @ X2).sum()
    delta_ = ((X.T @ X) ** 2).sum() / n ** 2
    beta = (beta_ / n - delta_) / (p * n)
    delta = (delta_ - 2 * mu * emp_cov_trace.sum() + p * mu ** 2) / p
    beta = min(beta, delta)
    shrinkage = 0.0 if beta == 0 else beta / delta
    cov = (1 - shrinkage) * emp_cov
    cov.flat[:: p + 1] += shrinkage * mu
    return cov


def shrunk_cov(hist):
    """Ledoit-Wolf covariance of daily returns, annualized (x252) so optimizer tolerances act on
    sensible magnitudes."""
    return ledoit_wolf(hist.values) * 252
