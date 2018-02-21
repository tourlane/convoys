import numpy # TODO: remove
from scipy.special import expit  # TODO: remove
import scipy.stats
import tensorflow as tf

from convoys.model import Model

class Regression(Model):
    # This will replace the model in __init__.py soon.
    def __init__(self, log_pdf, cdf, extra_params, L2_reg=1.0):
        self._L2_reg = L2_reg
        self._log_pdf = log_pdf
        self._cdf = cdf
        self._extra_params = extra_params
        self._sess = tf.Session()

        # TODO: this seems a bit dumb... is there no better way to support arbitrary number of dims?
        T_scalar_input = tf.placeholder(tf.float32, [])
        self._cdf_scalar_f = lambda t: self._sess.run(cdf(T_scalar_input), feed_dict={T_scalar_input: t})
        T_vector_input = tf.placeholder(tf.float32, [None])
        self._cdf_vector_f = lambda t: self._sess.run(cdf(T_vector_input), feed_dict={T_vector_input: t})

    def __del__(self):
        self._sess.close()

    def fit(self, X, B, T):
        # TODO: should do this in constructor, but the shape of X isn't known at that point
        n, k = X.shape

        X_input = tf.placeholder(tf.float32, [None, k])
        B_input = tf.placeholder(tf.float32, [None])
        T_input = tf.placeholder(tf.float32, [None])
        beta = tf.Variable(tf.zeros([k]), 'beta')

        X_prod_beta = tf.squeeze(tf.matmul(X_input, tf.expand_dims(beta, -1)), 1)
        c = tf.sigmoid(X_prod_beta)  # Conversion rates for each example

        LL_observed = tf.log(c) + self._log_pdf(T_input)
        LL_censored = tf.log((1-c) + c * (1 - self._cdf(T_input)))

        LL = tf.reduce_sum(B_input * LL_observed + (1 - B_input) * LL_censored, 0)
        LL_penalized = LL - self._L2_reg * tf.reduce_sum(beta * beta, 0)

        learning_rate_input = tf.placeholder(tf.float32, [])
        optimizer = tf.train.AdamOptimizer(learning_rate_input).minimize(-LL_penalized)

        # TODO(erikbern): this is going to add more and more variables every time we run this
        self._sess.run(tf.global_variables_initializer())

        best_cost, best_step, step = float('-inf'), 0, 0
        learning_rate = 0.1
        while True:
            feed_dict = {X_input: X, B_input: B, T_input: T, learning_rate_input: learning_rate}
            self._sess.run(optimizer, feed_dict=feed_dict)
            cost = self._sess.run(LL_penalized, feed_dict=feed_dict)
            if cost > best_cost:
                best_cost, best_step = cost, step
            if step - best_step > 100:
                learning_rate /= 10
                best_cost = float('-inf')
            if learning_rate < 1e-6:
                break
            step += 1
            if step % 100 == 0:
                print('step %6d (lr %6.6f): %9.2f' % (step, learning_rate, cost))

        self.params = dict(
            beta=self._sess.run(beta),
            beta_hessian=self._sess.run(
                -tf.hessians(LL_penalized, [beta])[0],
                feed_dict=feed_dict,
            ),
            **self._extra_params(self._sess)
        )

    def predict(self, x, t, ci=None):
        t = numpy.array(t)
        if len(t.shape) == 0:
            z = self._cdf_scalar_f(t)
        elif len(t.shape) == 1:
            z = self._cdf_vector_f(t)
        if ci:
            c, c_lo, c_hi = self.predict_final(x, ci)
            return (t, c*z, c_lo*z, c_hi*z)
        else:
            c = self.predict_final(x)
            return (t, c*z)

    def predict_final(self, x, ci=None):
        # TODO: should take advantage of tensorflow here!!!
        x = numpy.array(x)
        def f(x, d=0):
            return expit(numpy.dot(x, self.params['beta']) + d)
        if ci:
            inv_var = numpy.dot(numpy.dot(x.T, self.params['beta_hessian']), x)
            lo, hi = (scipy.stats.norm.ppf(p, scale=inv_var**-0.5) for p in ((1 - ci)/2, (1 + ci)/2))
            return f(x), f(x, lo), f(x, hi)
        else:
            return f(x)

    def predict_time(self):
        pass  # TODO: implement


class ExponentialRegression(Regression):
    def __init__(self, L2_reg=1.0):
        log_lambd_var = tf.Variable(tf.zeros([]), 'log_lambd')
        lambd = tf.exp(log_lambd_var)

        log_pdf = lambda T: tf.log(lambd) - T*lambd
        cdf = lambda T: 1 - tf.exp(-(T * lambd))

        return super(ExponentialRegression, self).__init__(
            log_pdf=log_pdf,
            cdf=cdf,
            extra_params=lambda sess: dict(lambd=sess.run(lambd)),
            L2_reg=L2_reg)


class WeibullRegression(Regression):
    def __init__(self, L2_reg=1.0):
        log_lambd_var = tf.Variable(tf.zeros([]), 'log_lambd')
        log_k_var = tf.Variable(tf.zeros([]), 'log_k')

        lambd = tf.exp(log_lambd_var)
        k = tf.exp(log_k_var)

        # PDF of Weibull: k * lambda * (x * lambda)^(k-1) * exp(-(t * lambda)^k)
        log_pdf = lambda T: tf.log(k) + tf.log(lambd) + (k-1)*(tf.log(T) + tf.log(lambd)) - (T*lambd)**k
        # CDF of Weibull: 1 - exp(-(t * lambda)^k)
        cdf = lambda T: 1 - tf.exp(-(T * lambd)**k)

        return super(WeibullRegression, self).__init__(
            log_pdf=log_pdf,
            cdf=cdf,
            extra_params=lambda sess: dict(k=sess.run(k),
                                           lambd=sess.run(lambd)),
            L2_reg=L2_reg)


class GammaRegression(Regression):
    def __init__(self, L2_reg=1.0):
        log_lambd_var = tf.Variable(tf.zeros([]), 'log_lambd')
        log_k_var = tf.Variable(tf.zeros([]), 'log_k')

        lambd = tf.exp(log_lambd_var)
        k = tf.exp(log_k_var)

        # PDF of gamma: 1.0 / gamma(k) * lambda ^ k * t^(k-1) * exp(-t * lambda)
        log_pdf = lambda T: -tf.lgamma(k) + k*tf.log(lambd) + (k-1)*tf.log(T) - lambd*T
        # CDF of gamma: gammainc(k, lambda * t)
        cdf = lambda T: tf.igamma(k, lambd * T)

        return super(GammaRegression, self).__init__(
            log_pdf=log_pdf,
            cdf=cdf,
            extra_params=lambda sess: dict(k=sess.run(k),
                                           lambd=sess.run(lambd)),
            L2_reg=L2_reg)