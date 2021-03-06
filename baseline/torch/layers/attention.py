import torch

from baseline.torch.utils import elu_clip

class MultiHeadReduction(torch.nn.Module):
    def __init__(self, input_dim, num_head):
        assert input_dim % num_head == 0
        super(MultiHeadReduction, self).__init__()
        self.input_dim = input_dim
        self.num_head = num_head

        self.fc_value = torch.nn.Linear(self.input_dim, self.input_dim)
        self.fc_attention = torch.nn.Linear(self.input_dim, self.num_head, bias=False)
        self._sqrt_input_dim = self.input_dim ** 0.5

    def forward(self, inputs, masks=None):
        """
        inputs: [batch_size, seq_len, input_dim]
        masks: [batch_size, seq_len]
        """
        batch_size, seq_len, _ = inputs.shape

        values = self.fc_value(inputs).view(batch_size, seq_len, self.num_head, self.input_dim//self.num_head) # [batch_size, seq_len, num_head, head_dim]
        u = self.fc_attention(inputs) # [batch_size, seq_len, num_head]
        exp_u = elu_clip(u / self._sqrt_input_dim).exp()
        if masks is not None:
            exp_u = exp_u * masks.unsqueeze(-1)
        attention = exp_u / exp_u.sum(1, keepdim=True) # [batch_size, seq_len, num_head]

        attend = (values * attention.unsqueeze(-1)).sum(1).view(batch_size, self.input_dim) # [batch_size, input_dim]
        return attend

class MultiHeadSelfAttention(torch.nn.Module):
    def __init__(self, input_dim, num_head):
        assert input_dim % num_head == 0
        super(MultiHeadSelfAttention, self).__init__()
        self.input_dim = input_dim
        self.num_head = num_head

        self.fc = torch.nn.Linear(self.input_dim, 3*self.input_dim)
        self._sqrt_input_dim = self.input_dim ** 0.5

    def forward(self, inputs, masks=None):
        """
        inputs: [batch_size, seq_len, input_dim]
        masks: [batch_size, seq_len]
        """
        batch_size, seq_len, _ = inputs.shape

        kqv = self.fc(inputs).view(batch_size, seq_len, 3, self.num_head, self.input_dim//self.num_head) # [batch_size, seq_len, 3, num_head, head_dim]
        keys, queries, values = kqv.unbind(2) # 3 * [batch_size, seq_len, num_head, head_dim]
        u = (queries.unsqueeze(2) * keys.unsqueeze(1)).sum(-1) # [batch_size, query_seq_len, key_seq_len, num_head]
        exp_u = elu_clip(u / self._sqrt_input_dim).exp()
        if masks is not None:
            exp_u = exp_u * masks.unsqueeze(1).unsqueeze(-1)
        attention = exp_u / exp_u.sum(2, keepdim=True) # [batch_size, query_seq_len, key_seq_len, num_head]

        attend = (values.unsqueeze(1) * attention.unsqueeze(-1)).sum(2).view(batch_size, seq_len, self.input_dim) # [batch_size, seq_len, input_dim]
        if masks is not None:
            attend = attend * masks.unsqueeze(-1)
        return attend

class MultiHeadAttention(torch.nn.Module):
    def __init__(self, input_dim, query_dim, num_head):
        assert input_dim % num_head == 0
        super(MultiHeadAttention, self).__init__()
        self.input_dim = input_dim
        self.query_dim = query_dim
        self.num_head = num_head

        self.fc_kv = torch.nn.Linear(self.input_dim, 2*self.input_dim)
        self.fc_query = torch.nn.Linear(self.query_dim, self.input_dim)
        self._sqrt_input_dim = self.input_dim ** 0.5

    def forward(self, inputs, queries, masks=None):
        """
        inputs: [batch_size, seq_len, input_dim]
        queries: [batch_size, num_query, query_dim]
        masks: [batch_size, seq_len]
        """
        batch_size, seq_len, _ = inputs.shape
        query_rank = len(queries.shape)
        assert query_rank in [2,3]

        kv = self.fc_kv(inputs).view(batch_size, 1, seq_len, 2, self.num_head, self.input_dim//self.num_head)
        k, v = kv.unbind(3) # 2 * [batch_size, 1, seq_len, num_head, head_dim]
        q = self.fc_query(queries).view(batch_size, -1, 1, self.num_head, self.input_dim//self.num_head) # [batch_size, num_query, 1, num_head, head_dim]
        u = (q * k).sum(-1) # [batch_size, num_query, seq_len, num_head]
        exp_u = elu_clip(u / self._sqrt_input_dim).exp()
        if masks is not None:
            exp_u = exp_u * masks.view(batch_size, 1, seq_len, 1)
        attention = exp_u / exp_u.sum(2, keepdim=True) # [batch_size, num_query, seq_len, num_head]

        attend = (v * attention.unsqueeze(-1)).sum(-3).view(batch_size, -1, self.input_dim) # [batch_size, num_query, input_dim]

        if query_rank == 2:
            attention = attention.squeeze(1) # [batch_size, seq_len, num_head]
            attend = attend.squeeze(1) # [batch_size, input_dim]

        return attend



