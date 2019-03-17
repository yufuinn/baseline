import numpy as np
import tensorflow as tf

def sinusoid_position_encoding(sequence_length:"[]", dim:"[]", dtype=tf.float32, wave=10000) -> "[sequence_length, dim]":
    half_dim = dim // 2
    d = tf.cast(tf.range(half_dim), dtype) # [dim//2]
    div = tf.pow(tf.cast(wave, dtype), d / tf.cast(half_dim, dtype=dtype)) # [dim//2]
    position = tf.cast(tf.range(sequence_length), dtype) # [seq_len]
    theta = tf.expand_dims(position, 1) / tf.expand_dims(div, 0) # [seq_len, dim//2]
    sin = tf.sin(theta)
    cos = tf.cos(theta)
    encoded = tf.concat([sin, cos], axis=1) # [seq_len, dim]
    encoded = tf.identity(encoded, name="sinusoid_position_encoding")
    return encoded


class MultiHeadAttention(tf.keras.layers.Layer):
    epsilon = 1e-10

    def __init__(self, attention_type:"dot-product/additive"="dot-product", use_bias=False, dim_additive=None, additive_activation="tanh", **kwargs):
        super(MultiHeadAttention, self).__init__(**kwargs)

        self.attention_type = str(attention_type)
        if self.attention_type == "dot-product":
            self.attend = self.dot_product_attend
        elif self.attention_type == "additive":
            assert dim_additive is not None
            self.dim_additive = dim_additive
            self.additive_activation = tf.keras.activations.get(additive_activation)
            self.attend = self.additive_attend
        else:
            raise ValueError("invalid attention_type: " + self.attention_type)
        self.use_bias = use_bias

        self.attention_built = False

    def call(self, inputs):
        raise NotImplementedError("MultiHeadAttention is the abstract class.")

    def attention_build(self, num_head=None, dim_query=None, dim_key=None):
        if self.attention_type == "dot-product":
            # if we use softmax_reduce, this bias substantially has no effect.
            if self.use_bias:
                self.attention_bias = self.add_weight(
                    name="attention_bias",
                    shape = [num_head],
                    initializer=tf.keras.initializers.zeros())

        elif self.attention_type == "additive":
            self.query_kernel = self.add_weight(
                name="query_kernel",
                shape=[num_head, dim_query, self.dim_additive],
                initializer=tf.keras.initializers.glorot_normal())
            self.key_kernel = self.add_weight(
                name="key_kernel",
                shape=[num_head, dim_key, self.dim_additive],
                initializer=tf.keras.initializers.glorot_normal())
            if self.use_bias:
                self.additive_bias = self.add_weight(
                    name="additive_bias",
                    shape = [num_head, self.dim_additive],
                    initializer=tf.keras.initializers.zeros())

            self.attention_kernel = self.add_weight(
                name="attention_kernel",
                shape=[num_head, self.dim_additive],
                initializer=tf.keras.initializers.glorot_normal())
            # if we use softmax_reduce, this bias substantially has no effect.
            if self.use_bias:
                self.attention_bias = self.add_weight(
                    name="attention_bias",
                    shape = [num_head],
                    initializer=tf.keras.initializers.zeros())
        else:
            raise ValueError("invalid attention_type: " + self.attention_type)
        self.attention_built = True

    def build(self, input_shape):
        assert self.attention_built, "must have called self.attention_build"
        super(MultiHeadAttention, self).build(input_shape)

    def dot_product_attend(self, queries:"[batch_size,query_seq_len,num_head,dim_key]", keys:"[batch_size,key_seq_len,num_head,dim_key]", values:"[batch_size,key_seq_len,num_head,dim_value]", key_mask:"[batch_size,key_seq_len]"=None):
        dtype = tf.dtypes.as_dtype(self.dtype or tf.keras.backend.floatx())
        dim_keys = tf.shape(keys)[-1]

        us = tf.reduce_sum(tf.expand_dims(queries, -3) * tf.expand_dims(keys, -4), axis=-1) / tf.sqrt(tf.cast(dim_keys, dtype)) # [batch_size, query_seq_len, key_seq_len, num_head]
        if self.use_bias:
            us = us + self.attention_bias

        reduction, self.attentions = self.softmax_reduce(scores=us, values=values, key_mask=key_mask)
        return reduction # [batch_size, query_seq_len, num_head*dim_value]

    def additive_attend(self, queries:"[batch_size,query_seq_len,num_head,dim_key]", keys:"[batch_size,key_seq_len,num_head,dim_key]", values:"[batch_size,key_seq_len,num_head,dim_value]", key_mask:"[batch_size,key_seq_len]"=None):
        h_query = tf.reduce_sum(tf.expand_dims(queries, -1) * self.query_kernel, axis=-2) # [batch_size, query_seq_len, num_head, dim_hidden]
        h_key = tf.reduce_sum(tf.expand_dims(keys, -1) * self.key_kernel, axis=-2) # [batch_size, key_seq_len, num_head, dim_hidden]
        h_additive = tf.expand_dims(h_query, -3) + tf.expand_dims(h_key, -4) # [batch_size, query_seq_len, key_seq_len, num_head, dim_hidden]
        if self.use_bias:
            h_additive = h_additive + self.additive_bias
        if self.additive_activation is not None:
            h_additive = self.additive_activation(h_additive)
        us = tf.reduce_sum(h_additive * self.attention_kernel, -1) # [batch_size, query_seq_len, key_seq_len, num_head]
        if self.use_bias:
            us = us + self.attention_bias

        reduction, self.attentions = self.softmax_reduce(scores=us, values=values, key_mask=key_mask)
        return reduction # [batch_size, query_seq_len, num_head*dim_value]

    def softmax_reduce(self, scores:"[batch_size, query_seq_len, key_seq_len, num_head]", values:"[batch_size, key_seq_len, num_head, dim_value]", key_mask:"[batch_size,key_seq_len]"=None):
        if key_mask is not None:
            dtype = tf.dtypes.as_dtype(self.dtype or tf.keras.backend.floatx())
            if key_mask.dtype != dtype:
                key_mask = tf.cast(key_mask, dtype)

        scores = scores - tf.reduce_max(scores, axis=-2, keepdims=True) # to avoid overflow

        exp_scores = tf.exp(scores) # [batch_size, query_seq_len, key_seq_len, num_head]
        if key_mask is not None:
            exp_scores = exp_scores * tf.expand_dims(tf.expand_dims(key_mask, -2), -1)
        attentions = exp_scores / (tf.reduce_sum(exp_scores, axis=-2, keepdims=True) + self.epsilon) # [batch_size, query_seq_len, key_seq_len, num_head]

        reduction = tf.reduce_sum(tf.expand_dims(attentions, -1) * tf.expand_dims(values, -4), axis=-3) # [batch_size, query_seq_len, num_head, dim_value]
        assert reduction.shape[-2:].is_fully_defined()
        reduction = tf.reshape(reduction, tf.unstack(tf.shape(reduction))[:-2] + [reduction.shape[-2]*reduction.shape[-1]]) # [batch_size, query_seq_len, num_head*dim_value]
        return reduction, attentions # [batch_size, query_seq_len, num_head*dim_value], [batch_size, query_seq_len, key_seq_len, num_head]

    def get_config(self):
        config = {
            "attention_type": self.attention_type,
            "use_bias": self.use_bias
            }
        if self.attention_type == "additive":
            config["dim_additive"] = self.dim_additive
            config["additive_activation"] = tf.keras.activations.serialize(self.additive_activation)
        base_config = super(MultiHeadAttention, self).get_config()
        return dict(list(base_config.items()) + list(config.items()))


class MultiHeadReduction(MultiHeadAttention):
    def __init__(self, dim_sum_output, num_head, position_type:"none/add"="none", use_bias=False, key_activation:"callable"=None, attention_type="dot-product", **kwargs):
        assert dim_sum_output % num_head == 0
        assert position_type in ["none", "add"]

        super(MultiHeadReduction, self).__init__(attention_type=attention_type, use_bias=use_bias, **kwargs)
        self.dim_sum_output = dim_sum_output
        self.num_head = num_head
        self.dim_each_output = self.dim_sum_output // self.num_head
        self.position_type = str(position_type)
        self.key_activation = tf.keras.activations.get(key_activation)

        self.supports_masking = True
        self.input_spec = tf.keras.layers.InputSpec(ndim=3)

    def build(self, input_shape):
        input_shape = tf.TensorShape(input_shape)
        input_shape = input_shape.with_rank(3)
        if input_shape[2].value is None:
            raise ValueError("The last dimension of the inputs must be defined: {}".format(input_shape))
        self.dim_input = input_shape[-1].value
        self.input_spec = tf.keras.layers.InputSpec(ndim=3, axes={2:self.dim_input})

        self.kv_kernel = self.add_weight(
            name="kv_kernel",
            shape=[self.dim_input, 2, self.num_head, self.dim_each_output],
            initializer=tf.keras.initializers.glorot_normal())
        if self.use_bias:
            self.value_bias = self.add_weight(
                name="value_bias",
                shape = [self.num_head, self.dim_each_output],
                initializer=tf.keras.initializers.zeros())

        self.query_kernel = self.add_weight(
            name="query_kernel",
            shape=[self.num_head, self.dim_each_output],
            initializer=tf.keras.initializers.random_normal(mean=0.0, stddev=1.0))

        super(MultiHeadReduction, self).attention_build(num_head=self.num_head, dim_query=self.dim_each_output, dim_key=self.dim_each_output)
        super(MultiHeadReduction, self).build(input_shape)

    def call(self, inputs:"[batch_size,seq_len,dim_input]", mask:"[batch_size, seq_len]"=None):
        if self.position_type == "add":
            max_seq_len = tf.shape(inputs)[1]
            dtype = tf.dtypes.as_dtype(self.dtype or tf.keras.backend.floatx())
            dim_input = inputs.shape[2] if inputs.shape[2].value is not None else tf.shape(inputs)[2]
            inputs = inputs + sinusoid_position_encoding(sequence_length=max_seq_len, dim=dim_input, dtype=dtype)

        kvs = tf.tensordot(inputs, self.kv_kernel, 1) # [batch_size, seq_len, 2, num_head, dim_each_output]
        keys, values = tf.unstack(kvs, axis=2) # 2x[batch_size, seq_len, num_head, dim_each_output]
        if self.use_bias:
            values = values + self.value_bias
        if self.key_activation is not None:
            keys = self.key_activation(keys)

        reduction = tf.squeeze(self.dot_product_attend(keys=keys, values=values, queries=self.query_kernel, key_mask=mask), 1) # [batch_size, dim_sum_output]
        return reduction # [batch_size, dim_sum_output]

    def compute_output_shape(self, input_shape):
        input_shape = tf.TensorShape(input_shape)
        input_shape = input_shape.with_rank(3)
        if input_shape[2].value is None:
            raise ValueError("The last dimension of the inputs must be defined: {}".format(input_shape))
        return input_shape[:1].concatenate(self.dim_sum_output)

    def compute_mask(self, inputs, mask=None):
        return # no more mask

    def get_config(self):
        config = {
            "dim_sum_output": self.dim_sum_output,
            "num_head": self.num_head,
            "position_type": self.position_type,
            "key_activation": tf.keras.activations.serialize(self.key_activation)
            }
        base_config = super(MultiHeadReduction, self).get_config()
        return dict(list(base_config.items()) + list(config.items()))


class MultiHeadSelfAttention:
    epsilon = 1e-10
    def __init__(self, dim_sum_output, num_head, position_type:"none/add"="none", use_bias=False):
        assert dim_sum_output % num_head == 0
        assert position_type in ["none", "add"]

        self.dim_sum_output = dim_sum_output
        self.num_head = num_head
        self.dim_each_output = self.dim_sum_output // self.num_head
        self.position_type = str(position_type)
        self.use_bias = use_bias

        initializer = tf.glorot_normal_initializer()
        self.fc_kvqs = tf.keras.layers.Dense(3*self.dim_sum_output, use_bias=self.use_bias, kernel_initializer=initializer)
        self._root_d_k = tf.constant(np.sqrt(self.dim_each_output), dtype=tf.float32)

    def __call__(self, inputs:"[batch_size,seq_len,dim_input]", seq_lens:"[batch_size]", batch_size:"[]"=None, max_seq_len:"[]"=None):
        if batch_size is None: batch_size = tf.shape(inputs)[0]
        if max_seq_len is None: max_seq_len = tf.shape(inputs)[1]
        seq_mask = tf.sequence_mask(seq_lens, max_seq_len, dtype=tf.float32) # [batch_size, seq_len]

        if self.position_type == "add":
            dim_input = inputs.shape[2] if inputs.shape[2].value is not None else tf.shape(inputs)[2]
            inputs = inputs + sinusoid_position_encoding(sequence_length=max_seq_len, dim=dim_input)

        concat_kvqs = self.fc_kvqs(inputs) # [batch_size, seq_len, 3*num_head*dim_each_output]
        kvqs = tf.reshape(concat_kvqs, [batch_size, max_seq_len, 3*self.num_head, self.dim_each_output])
        keys, values, queries = tf.split(kvqs, 3, axis=2) # 3x[batch_size, seq_len, num_head, dim_each_output]

        us = tf.reduce_sum(keys[:,tf.newaxis]*queries[:,:,tf.newaxis], axis=-1, keepdims=True) / self._root_d_k # [batch_size, query_seq_len, key_seq_len, num_head, 1]
        us = us - tf.reduce_max(us, axis=2, keepdims=True) # to avoid overflow
        exp_us = tf.exp(us) * seq_mask[:,:,tf.newaxis,tf.newaxis,tf.newaxis] * seq_mask[:,tf.newaxis,:,tf.newaxis,tf.newaxis] # [batch_size, query_seq_len, key_seq_len, num_head, 1]
        attentions = exp_us / (tf.reduce_sum(exp_us, axis=2, keepdims=True) + self.epsilon) # [batch_size, query_seq_len, key_seq_len, num_head, 1]

        reduction = tf.reshape(tf.reduce_sum(values[:,tf.newaxis] * attentions, axis=2), [batch_size, max_seq_len, self.dim_sum_output])
        self.attentions = tf.squeeze(attentions, axis=-1) # [batch_size, query_seq_len, key_seq_len, num_head]
        return reduction # [batch_size, max_seq_len, dim_sum_output]





