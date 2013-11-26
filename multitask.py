import numpy as np
from gridworld import *
from mdp_solver import *
import random
from scipy.stats import chi2
from mdp_solver import value_iteration_to_policy

class MdpClass(object):
    def __init__(self, class_id, weights_mean, weights_cov):
        self.class_id = class_id
        self.weights_mean = weights_mean
        self.weights_cov = weights_cov
        self.inv_weights_cov = np.linalg.inv(weights_cov)

    def likelihood(self, weights):
        multiplier = 1./math.sqrt((2.*math.pi)**self.weights_cov.shape[0]*np.linalg.det(self.weights_cov))
        exponent = -0.5 * np.dot(np.dot(np.transpose(weights - self.weights_mean), self.inv_weights_cov), weights - self.weights_mean)
        return multiplier * math.exp(exponent)

    def sample(self):
        return np.random.multivariate_normal(self.weights_mean, self.weights_cov)

    def sample_posterior(self, states, rewards):
        """
        We have the product of two Gaussians, so we can derive a closed form update for the posterior.
        """
        y = self.inv_weights_cov + np.dot(np.transpose(states), states)
        post_cov = np.linalg.inv(y)
        post_mean = np.dot(np.linalg.inv(y), np.dot(self.inv_weights_cov, self.weights_mean) + np.dot(np.transpose(states), rewards))
        return np.random.multivariate_normal(post_mean, post_cov)

class NormalInverseWishartDistribution(object):
    def __init__(self, mu, lmbda, nu, psi):
        self.mu = mu
        self.lmbda = lmbda
        self.nu = nu
        self.psi = psi
        self.inv_psi = np.linalg.inv(psi)

    def sample(self):
        return (np.random.multivariate_normal(self.mu, self.inv_psi / self.lmbda), np.linalg.inv(self.wishartrand()))
 
    def wishartrand(self):
        dim = self.inv_psi.shape[0]
        chol = np.linalg.cholesky(self.inv_psi)
        foo = np.zeros((dim,dim))
        
        for i in range(dim):
            for j in range(i+1):
                if i == j:
                    foo[i,j] = np.sqrt(chi2.rvs(self.nu-(i+1)+1))
                else:
                    foo[i,j]  = np.random.normal(0,1)
        return np.dot(chol, np.dot(foo, np.dot(foo.T, chol.T)))

class LinearGaussianRewardModel(object):
    """
    A model of the rewards for experiment 1 in the Wilson et al. paper. See section 4.4 for implementation details.
    """
    def __init__(self, num_colors, reward_stdev, classes, assignments, auxillary_distribution, alpha=0.5, m=2, burn_in=100, mcmc_samples=500, thin=10):
        self.weights_size = num_colors * NUM_RELATIVE_CELLS
        self.reward_stdev = reward_stdev
        self.classes = classes
        self.assignments = assignments
        self.total_mpds = sum(assignments) + 1
        self.auxillary_distribution = auxillary_distribution
        self.alpha = alpha
        self.m = m
        self.burn_in = burn_in
        self.mcmc_samples = mcmc_samples
        self.thin = thin
        assert(len(classes) == len(assignments))
        self.states = []
        self.rewards = []
        # Create auxillary classes in case this MDP is from an unseen distribution
        # Note: This has to be done once for the whole environment in order to be compatible
        # with the practical hack of update_beliefs. Since we don't reassign anything other than
        # the current MDP, if we add a new auxillary to the list of classes, we'll just get
        # 0 assignments on the next step and thus 0 probability. Instead, we have to keep it
        # as alpha/m probability at each step so it always can be selected. This seems unfortunate
        # since it means the agent cannot adapt its auxillary distributions much. The best
        # we can do is to get rid of the ones that were not MAP classes last step and keep
        # one only if it was the MAP class.
        self.auxillaries = [self.sample_auxillary(len(self.classes) + i) for i in range(self.m)]
        c = self.proportional_selection(self.assignments + [self.alpha / self.m for _ in self.auxillaries])
        self.map_class = (self.classes + self.auxillaries)[c]
        self.weights = self.map_class.sample()
        

    def add_observation(self, state, reward):
        self.states.append(state)
        self.rewards.append(reward)

    def update_beliefs(self):
        """
        Implements the efficient approximation of Algorithm 2 from Wilson et al.
        described in section 4.4. to update the model parameters during an episode.
        """
        states = np.array(self.states)
        rewards = np.array(self.rewards)
        samples = np.zeros(len(self.classes)+self.m)
        c = self.proportional_selection(self.assignments + [self.alpha / self.m for _ in self.auxillaries])
        mdp_class = (self.classes + self.auxillaries)[c]
        w = mdp_class.sample_posterior(states, rewards)
        for i in range(self.mcmc_samples):
            mdp_class = self.sample_assignment(w)
            w = mdp_class.sample_posterior(states, rewards)
            if i >= self.burn_in and i % self.thin == 0:
                samples[mdp_class.class_id] += 1
        print 'Assignment Distribution: {0} Original: {1}'.format(samples, c)
        # MAP calculations
        map_c = np.argmax(samples)
        if map_c >= len(self.classes):
            # We are keeping this auxillary class
            new_class = self.auxillaries[map_c - len(self.classes)]
            new_class.class_id = len(self.classes)
            self.map_class = new_class
            # None of the other auxillary classes were good enough -- resample them
            self.auxillaries = [new_class] + [self.sample_auxillary(len(self.classes) + i + 1) for i in range(self.m - 1)]
        else:
            self.map_class = self.classes[map_c]
            # None of the auxillary classes were good enough -- resample them
            self.auxillaries = [self.sample_auxillary(len(self.classes) + i) for i in range(self.m)]
        self.weights = self.sample_weights(states, rewards)

    def sample_assignment(self, weights):
        """
        Implements Algorithm 3 from the Wilson et al. paper.
        """
        classes = [c for c in self.classes] # duplicate classes
        # Calculate likelihood of assigning to a known class
        assignment_probs = [self.assignments[i] / (self.total_mpds - 1. + self.alpha) * self.classes[i].likelihood(weights) for i in range(len(self.classes))]
        # Calculate likelihood of assigning to a new, unknown class with the default prior
        for i,aux in enumerate(self.auxillaries):
            assignment_probs.append(self.alpha / float(self.m) / (self.total_mpds - 1. + self.alpha) * aux.likelihood(weights))
            classes.append(aux) # add auxillary classes to the list of options

        # Sample an assignment proportional to the likelihoods
        return classes[self.proportional_selection(assignment_probs)]

    def proportional_selection(self, proportions):
        partition = sum(proportions)
        if partition == 0:
            print 'ERROR: Partition == 0. Proportions: {0}'.format(proportions)
        proportions = [x / partition for x in proportions]
        u = random.random()
        cur = 0.
        for i,prob in enumerate(proportions):
            cur += prob
            if u <= cur:
                return i

    def sample_auxillary(self, class_id):
        (mean, cov) = self.auxillary_distribution.sample()
        return MdpClass(class_id, mean, cov)

    def sample_weights(self, states, rewards):
        self.weights = self.map_class.sample_posterior(states, rewards)

class MultiTaskBayesianAgent(Agent):
    """
    A Bayesian RL agent that infers a hierarchy of MDP distributions, with a top-level
    class distribution which parameterizes each bottom-level MDP distribution.

    TODO: Currently the agent assumes all MDPs are observed sequentially. Extending the
    algorithm to handle multiple, simultaneous MDPs may require non-trivial changes.
    """
    def __init__(self, width, height, num_colors, num_domains, reward_stdev, name=None, steps_per_policy=1, num_auxillaries=2, goal_known=True):
        super(MultiTaskBayesianAgent, self).__init__(width, height, num_colors, num_domains, name)
        self.reward_stdev = reward_stdev
        self.steps_per_policy = steps_per_policy
        self.num_auxillaries = num_auxillaries
        self.goal_known = goal_known
        self.state_size = num_colors * NUM_RELATIVE_CELLS
        self.auxillary = NormalInverseWishartDistribution(np.zeros(self.state_size), 1., self.state_size+1, np.identity(self.state_size))
        self.classes = []
        self.assignments = []
        self.model = LinearGaussianRewardModel(num_colors, self.reward_stdev, self.classes, self.assignments, self.auxillary, m=num_auxillaries)
        self.cur_mdp = 0
        self.steps_since_update = 0

    def episode_starting(self, idx, state):
        super(MultiTaskBayesianAgent, self).episode_starting(idx, state)
        if idx is not self.cur_mdp:
            self.cur_mdp = idx
            self.steps_since_update = 0
            self.update_beliefs()

    def episode_over(self, idx):
        assert(idx == self.cur_mdp)
        super(MultiTaskBayesianAgent, self).episode_over(idx)
        # TODO: Handle unknown goal locations

    def get_action(self, idx):
        assert(idx == self.cur_mdp)
        if self.steps_since_update >= self.steps_per_policy:
            self.update_policy()
        self.steps_since_update += 1
        return self.policy[self.state[idx]]

    def set_state(self, idx, state):
        assert(idx == self.cur_mdp)
        super(MultiTaskBayesianAgent, self).set_state(idx, state)

    def observe_reward(self, idx, r):
        assert(idx == self.cur_mdp)
        super(MultiTaskBayesianAgent, self).observe_reward(idx, r)
        self.model.add_observation(self.state[idx], r)

    def update_beliefs(self):
        """
        Implements Algorithm 2 from Wilson et al. to update the beliefs
        over all MDPs.

        Note that the beliefs of past MDPs are only updated between episodes,
        for efficiency. See section 4.4 for details on the efficiency issue.
        """
        pass

    def update_policy(self):
        """
        Algorithm 1, Line 5 from Wilson et al.
        """
        self.model.update_beliefs()
        weights = self.model.weights
        cell_values = np.zeros((self.width, self.height))
        # TODO: Calculate cell values from weight vector
        # TODO: Handle unknown goal locations by enabling passing a belief distribution over goal locations
        self.policy = value_iteration_to_policy(self.width, self.height, self.domains[self.cur_mdp].goal, cell_values)

if __name__ == "__main__":
    TRUE_CLASS = 0
    SAMPLE_SIZE = 100
    COLORS = 2
    RSTDEV = 0.3
    SIZE = COLORS * NUM_RELATIVE_CELLS
    NUM_DISTRIBUTIONS = 4

    niw_true = NormalInverseWishartDistribution(np.zeros(SIZE) - 3., 1., SIZE+1, np.identity(SIZE))
    true_params = [niw_true.sample() for _ in range(NUM_DISTRIBUTIONS)]
    classes = [MdpClass(i, mean, cov) for i,(mean,cov) in enumerate(true_params)]
    assignments = [1. for _ in classes]
    auxillary = NormalInverseWishartDistribution(np.zeros(SIZE), 1., SIZE+1, np.identity(SIZE))

    candidate_params = [auxillary.sample() for _ in range(NUM_DISTRIBUTIONS)]
    candidate_classes = [MdpClass(i, mean, cov) for i,(mean,cov) in enumerate(candidate_params)]
    model = LinearGaussianRewardModel(COLORS, RSTDEV, classes, assignments, auxillary)

    weights = classes[TRUE_CLASS].sample()

    print 'True class: {0}'.format(TRUE_CLASS)

    for s in range(SAMPLE_SIZE):
        q_sample = np.zeros((COLORS * NUM_RELATIVE_CELLS))
        for row in range(NUM_RELATIVE_CELLS):
            q_sample[row * COLORS + random.randrange(COLORS)] = 1
        r_sample = np.random.normal(loc=np.dot(weights, q_sample), scale=RSTDEV)
        model.add_observation(q_sample, r_sample)
        model.update_beliefs()
        print 'Samples: {0} Class belief: {1}'.format(s+1, model.map_class.class_id)