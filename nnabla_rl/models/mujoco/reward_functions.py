import nnabla as nn

import nnabla.functions as NF
import nnabla.parametric_functions as NPF

import nnabla_rl.initializers as RI
from nnabla_rl.models.reward_function import RewardFunction


class GAILDiscriminator(RewardFunction):
    '''
    discriminator model used as reward function proposed by Jonathan Ho, et al.
    See: https://arxiv.org/pdf/1606.03476.pdf
    '''

    def __init__(self, scope_name: str):
        super(GAILDiscriminator, self).__init__(scope_name)

    def r(self, s_current: nn.Variable, a_current: nn.Variable, s_next: nn.Variable) -> nn.Variable:
        '''
        Notes:
            In gail, we don't use the next state.
        '''
        h = NF.concatenate(s_current, a_current, axis=1)
        with nn.parameter_scope(self.scope_name):
            h = NPF.affine(h, n_outmaps=100, name="linear1",
                           w_init=RI.GlorotUniform(h.shape[1], 100))
            h = NF.tanh(x=h)
            h = NPF.affine(h, n_outmaps=100, name="linear2",
                           w_init=RI.GlorotUniform(h.shape[1], 100))
            h = NF.tanh(x=h)
            h = NPF.affine(h, n_outmaps=1, name="linear3",
                           w_init=RI.GlorotUniform(h.shape[1], 1))

        return h
