import uuid
import tensorflow as tf
import numpy as np
from tensorflow.python.ops import math_ops
from tensorflow.python.ops import array_ops
from tensorflow.python.ops import variable_scope as vs
from tensorflow.contrib.rnn import RNNCell, LSTMStateTuple
#from tensorflow.contrib.rnn.python.ops.core_rnn_cell_impl import _linear
#from tensorflow.python.ops.rnn_cell_impl import _linear
import matplotlib.pyplot as plt


def ln(tensor, scope=None, epsilon=1e-5):
    """ Layer normalizes a 2D tensor along its second axis """
    assert (len(tensor.get_shape()) == 2)
    m, v = tf.nn.moments(tensor, [1], keep_dims=True)
    if not isinstance(scope, str):
        scope = ''
    with tf.variable_scope(scope + 'layer_norm'):
        scale = tf.get_variable('scale',
                                shape=[tensor.get_shape()[1]],
                                initializer=tf.constant_initializer(1))
        shift = tf.get_variable('shift',
                                shape=[tensor.get_shape()[1]],
                                initializer=tf.constant_initializer(0))
    ln_initial = (tensor - m) / tf.sqrt(v + epsilon)

    return ln_initial * scale + shift


class MultiDimensionalLSTMCell(RNNCell):
    """
    Adapted from TF's BasicLSTMCell to use Layer Normalization.
    Note that state_is_tuple is always True.
    """

    TIME_STEP = 0

    def __init__(self, num_units, forget_bias=0.0, activation=tf.nn.tanh):
        self._num_units = num_units
        self._forget_bias = forget_bias
        self._activation = activation

    @property
    def state_size(self):
        return LSTMStateTuple(self._num_units, self._num_units)

    @property
    def output_size(self):
        return self._num_units

    def time_step(self):
        MultiDimensionalLSTMCell.TIME_STEP += 1
        return MultiDimensionalLSTMCell.TIME_STEP

    def __call__(self, inputs, state, scope=None):
        """Long short-term memory cell (LSTM).
        @param: inputs (batch,n)
        @param state: the states and hidden unit of the two cells
        """
        with tf.variable_scope(scope or type(self).__name__):
            c1, c2, h1, h2 = state

            # change bias argument to False since LN will add bias via shift
            weights = vs.get_variable(
                'kernel', [inputs.get_shape()[1] + h1.get_shape()[1] + h2.get_shape()[1], 5 * self._num_units],
                dtype=inputs.dtype,
                initializer=None)
            concat = math_ops.matmul(array_ops.concat([inputs, h1, h2], 1), weights)
            #concat = _linear([inputs, h1, h2], 5 * self._num_units, False)

            i, j, f1, f2, o = tf.split(value=concat, num_or_size_splits=5, axis=1)

            # add layer normalization to each gate
            i = ln(i, scope='i/')
            j = ln(j, scope='j/')
            f1 = ln(f1, scope='f1/')
            f2 = ln(f2, scope='f2/')
            o = ln(o, scope='o/')

            # gate activation
            i = tf.nn.sigmoid(i)
            f1 = tf.nn.sigmoid(f1 + self._forget_bias)
            f2 = tf.nn.sigmoid(f2 + self._forget_bias)
            o = tf.nn.sigmoid(o)

            # gate summary
            #tf.summary.histogram('forget1', f1)
            #tf.summary.histogram('forget2', f2)
            #tf.contrib.summary.histogram('forget1_{}'.format(self.time_step()), f1)
            #tf.contrib.summary.histogram('forget2_{}'.format(self.time_step()), f2)

            new_c = (c1 * f1 + c2 * f2 + i * self._activation(j))

            # add layer_normalization in calculation of new hidden state
            new_h = self._activation(ln(new_c, scope='new_h/')) * o
            new_state = LSTMStateTuple(new_c, new_h)

            return new_h, new_state


def multi_dimensional_rnn_while_loop(rnn_size, input_data, sh, dims=None, scope_n="layer1"):
    """Implements naive multi dimension recurrent neural networks

    @param rnn_size: the hidden units
    @param input_data: the data to process of shape [batch,h,w,channels]
    @param sh: [height,width] of the windows
    @param dims: dimensions to reverse the input data,eg.
        dims=[False,True,True,False] => true means reverse dimension
    @param scope_n : the scope

    returns [batch,h/sh[0],w/sh[1],rnn_size] the output of the lstm
    """
    
    with tf.variable_scope("MultiDimensionalLSTMCell-" + scope_n):
        
        # Create multidimensional cell with selected size
        cell = MultiDimensionalLSTMCell(rnn_size)

        # Get the shape of the imput (batch_size, x, y, channels)
        shape = input_data.get_shape().as_list()
        batch_size = shape[0]
        X_dim = shape[1]
        Y_dim = shape[2]
        channels = shape[3]
        # Window size
        X_win = sh[0]
        Y_win = sh[1]
        # Get the runtime batch size
        batch_size_runtime = tf.shape(input_data)[0]
        
        # If the imput cannot be exactly sampled by the window, we patch it with zeros
        if X_dim % X_win != 0:
            # Get offset size
            offset = tf.zeros([batch_size_runtime, X_win - (X_dim % X_win), Y_dim, channels])
            # Concatenate X dimension
            input_data = tf.concat(axis=1, values=[input_data, offset])
            # Get new shape
            shape = input_data.get_shape().as_list()
            # Update shape value
            X_dim = shape[1]
            
        # The same but for Y axis
        if Y_dim % Y_win != 0:
            # Get offset size
            offset = tf.zeros([batch_size_runtime, X_dim, Y_win - (Y_dim % Y_win), channels])
            # Concatenate Y dimension
            input_data = tf.concat(axis=2, values=[input_data, offset])
            # Get new shape
            shape = input_data.get_shape().as_list()
            # Update shape value
            Y_dim = shape[2]

        # Get the steps to perform in X and Y axis
        h, w = int(X_dim / X_win), int(Y_dim / Y_win)
        
        # Get the number of features (total number of imput values per step)
        features = Y_win * X_win * channels
        
        

    
        # Reshape input data to a tensor containing the step indexes and features inputs
        # The batch size is inferred from the tensor size
        x = tf.reshape(input_data, [batch_size_runtime, h, w, features])
        
        # Reverse the selected dimensions
        if dims is not None:
            assert dims[0] is False and dims[3] is False
            x = tf.reverse(x, dims)
            
        # Reorder inputs to (h, w, batch_size, features)
        x = tf.transpose(x, [1, 2, 0, 3])
        # Reshape to a one dimensional tensor of (h*w*batch_size , features)
        x = tf.reshape(x, [-1, features])
        # Split tensor into h*w tensors of size (batch_size , features)
        x = tf.split(axis=0, num_or_size_splits=h * w, value=x)
        
        # Create an input tensor array (literally an array of tensors) to use inside the loop
        inputs_ta = tf.TensorArray(dtype=tf.float32, size=h * w, name='input_ta')
        # Unestack the input X in the tensor array
        inputs_ta = inputs_ta.unstack(x)
        # Create an input tensor array for the states
        states_ta = tf.TensorArray(dtype=tf.float32, size=h * w + 1, name='state_ta', clear_after_read=False)
        # And an other for the output
        outputs_ta = tf.TensorArray(dtype=tf.float32, size=h * w, name='output_ta')

        # initial cell hidden states
        # Write to the last position of the array, the LSTMStateTuple filled with zeros
        states_ta = states_ta.write(h * w, LSTMStateTuple(tf.zeros([batch_size_runtime, rnn_size], tf.float32),
                                                          tf.zeros([batch_size_runtime, rnn_size], tf.float32)))


        
        # Function to get the sample skipping one row
        def get_up(t_, w_):
            return t_ - tf.constant(w_)
        # Function to get the previous sample
        def get_last(t_, w_):
            return t_ - tf.constant(1)

        # Controls the initial index
        time = tf.constant(0)
        zero = tf.constant(0)

        # Body of the while loop operation that aplies the MD LSTM
        def body(time_, outputs_ta_, states_ta_):
            
            # If the current position is less or equal than the width, we are in the first row
            # and we need to read the zero state we added in row (h*w). 
            # If not, get the sample located at a width dstance.
            state_up = tf.cond(tf.less(time_, tf.constant(w)),
                               lambda: states_ta_.read(h * w),
                              lambda: states_ta_.read(get_up(time_, w)))
            
            # If it is the first step we read the zero state if not we read the inmediate last
            state_last = tf.cond(tf.less(zero, tf.mod(time_, tf.constant(w))),
                                 lambda: states_ta_.read(get_last(time_, w)),
                                 lambda: states_ta_.read(h * w))

            # We build the input state in both dimensions
            current_state = state_up[0], state_last[0], state_up[1], state_last[1]
            # Now we calculate the output state and the cell output
            out, state = cell(inputs_ta.read(time_), current_state)
            # We write the output to the output tensor array
            outputs_ta_ = outputs_ta_.write(time_, out)
            # And save the output state to the state tensor array
            states_ta_ = states_ta_.write(time_, state)
            
            # Return outputs and incremented time step 
            return time_ + 1, outputs_ta_, states_ta_

        
        # Loop output condition. The index, given by the time, should be less than the 
        # total number of steps defined within the image
        def condition(time_, outputs_ta_, states_ta_):
            return tf.less(time_, tf.constant(h * w))

        # Run the looped operation
        result, outputs_ta, states_ta = tf.while_loop(condition, body, [time, outputs_ta, states_ta],
                                                      parallel_iterations=1)

        # Extract the output tensors from the processesd tensor array
        outputs = outputs_ta.stack()
        states = states_ta.stack()

        # Reshape outputs to match the shape of the imput
        y = tf.reshape(outputs, [h, w, batch_size_runtime, rnn_size])

        # Reorder te dimensions to match the input
        y = tf.transpose(y, [2, 0, 1, 3])
        # Reverse if selected
        if dims is not None:
            y = tf.reverse(y, dims)

        # Return the output and the inner states
        return y, states


def multi_dimensional_rnn_static(rnn_size, input_data, sh, dims=None, scope_n="layer1"):
    """Implements naive multi dimension recurrent neural networks

    @param rnn_size: the hidden units
    @param input_data: the data to process of shape [batch,h,w,channels]
    @param sh: [height,width] of the windows
    @param dims: dimensions to reverse the input data,eg.
        dims=[False,True,True,False] => true means reverse dimension
    @param scope_n : the scope

    returns [batch,h/sh[0],w/sh[1],rnn_size] the output of the lstm
    """

    with tf.variable_scope("MultiDimensionalLSTMCell-" + scope_n):

        # Create multidimensional cell with selected size
        cell = MultiDimensionalLSTMCell(rnn_size)

        # Get the shape of the imput (batch_size, x, y, channels)
        shape = input_data.get_shape().as_list()
        batch_size = shape[0]
        X_dim = shape[1]
        Y_dim = shape[2]
        channels = shape[3]
        # Window size
        X_win = sh[0]
        Y_win = sh[1]
        # Get the runtime batch size
        batch_size_runtime = tf.shape(input_data)[0]

        # If the imput cannot be exactly sampled by the window, we patch it with zeros
        if X_dim % X_win != 0:
            # Get offset size
            offset = tf.zeros([batch_size_runtime, X_win - (X_dim % X_win), Y_dim, channels])
            # Concatenate X dimension
            input_data = tf.concat(axis=1, values=[input_data, offset])
            # Get new shape
            shape = input_data.get_shape().as_list()
            # Update shape value
            X_dim = shape[1]

        # The same but for Y axis
        if Y_dim % Y_win != 0:
            # Get offset size
            offset = tf.zeros([batch_size_runtime, X_dim, Y_win - (Y_dim % Y_win), channels])
            # Concatenate Y dimension
            input_data = tf.concat(axis=2, values=[input_data, offset])
            # Get new shape
            shape = input_data.get_shape().as_list()
            # Update shape value
            Y_dim = shape[2]

        # Get the steps to perform in X and Y axis
        h, w = int(X_dim / X_win), int(Y_dim / Y_win)

        # Get the number of features (total number of imput values per step)
        features = Y_win * X_win * channels

        # Reshape input data to a tensor containing the step indexes and features inputs
        # The batch size is inferred from the tensor size
        x = tf.reshape(input_data, [batch_size_runtime, h, w, features])

        # Reverse the selected dimensions
        if dims is not None:
            assert dims[0] is False and dims[3] is False
            x = tf.reverse(x, dims)

        # Reorder inputs to (h, w, batch_size, features)
        x = tf.transpose(x, [1, 2, 0, 3])
        # Reshape to a one dimensional tensor of (h*w*batch_size , features)
        x = tf.reshape(x, [-1, features])
        # Split tensor into h*w tensors of size (batch_size , features)
        x = tf.split(axis=0, num_or_size_splits=h * w, value=x)

        # static loop
        states = []
        outputs = []
        def get_lstm_zero_state(bs, rnn_size):
            return LSTMStateTuple(tf.zeros([bs, rnn_size], tf.float32),
                                  tf.zeros([bs, rnn_size], tf.float32))
        for i in range(h * w):
            if i > 0:
                vs.get_variable_scope().reuse_variables()
            state_up = get_lstm_zero_state(batch_size_runtime, rnn_size) \
                if i < w else states[i - w]
            state_last = get_lstm_zero_state(batch_size_runtime, rnn_size) \
                if i % w == 0 else states[i - 1]
            current_state = state_up[0], state_last[0], state_up[1], state_last[1]
            out, state = cell(x[i], current_state)
            outputs.append(out)
            states.append(state)
        outputs = tf.stack(outputs)

        # Reshape outputs to match the shape of the imput
        y = tf.reshape(outputs, [h, w, batch_size_runtime, rnn_size])

        # Reorder te dimensions to match the input
        y = tf.transpose(y, [2, 0, 1, 3])
        # Reverse if selected
        if dims is not None:
            y = tf.reverse(y, dims)

        # Return the output and the inner states
        return y, states


class RNNVis(object):
    def __init__(self):
        pass


    def set_row_col(self, row, col):
        self.row = row
        self.col = col


    def plot_hidden_grad(self, hidden_state, hidden_state_grad, sequence=None,
                         hidden_state_wrt_input_grad=None, shape=None, activation='tanh'):
        '''
        @param sequence:
        @param hidden_state_grad: [batch_size, h*w, rnn_size]
        @param hidden_state_wrt_input_grad: [batch_size, 2, rnn_size, input_size (channel)]
        @param shape:
        @return:
        '''
        bs, l, h = hidden_state_grad.shape
        if shape == None:
            shape = [1, 1]
        elif len(shape) != 2 or np.prod(shape) > l or l % np.prod(shape) != 0:
            raise Exception('shape not right')
        step = l // np.prod(shape)
        hidden_state_grad = np.abs(hidden_state_grad)
        gmax = np.max(hidden_state_grad)
        print('hidden state grad max: {}'.format(gmax))
        shape[0] *= 2
        add_row = sequence is not None or hidden_state_wrt_input_grad is not None
        if hidden_state_wrt_input_grad is not None:
            hidden_state_wrt_input_grad = np.transpose(hidden_state_wrt_input_grad, [0, 2, 1, 3])
            hidden_state_wrt_input_grad = np.reshape(hidden_state_wrt_input_grad, [bs, h, -1])
            hidden_state_wrt_input_grad = np.abs(hidden_state_wrt_input_grad)
        if add_row:
            shape[0] += 1
        fig, axes = plt.subplots(shape[0], shape[1], figsize=(10, 8))
        axes = axes.reshape(shape)
        for b in range(bs):
            if activation == 'tanh':
                hmin, hmax = -1, 1
            else:
                hmin, hmax = np.min(hidden_state[b]), np.max(hidden_state[b])
            print('hidden state activation max: {}, min: {}'.format(hmax, hmin))
            gmax = np.max(hidden_state_grad[b])
            for r in range(shape[0]):
                if add_row and r == shape[0] - 1:
                    ri = 0
                    if sequence is not None:
                        ax = axes[r, ri]
                        ax.imshow(sequence[b], vmin=np.min(sequence[b]), vmax=np.max(sequence[b]),
                                  interpolation='none', cmap='gray')
                        ax.set_xticks([])
                        ax.set_yticks([])
                        ri += 1
                    if hidden_state_wrt_input_grad is not None:
                        ax = axes[r, ri]
                        ax.imshow(hidden_state_wrt_input_grad[b], vmin=0, vmax=np.max(hidden_state_wrt_input_grad[b]),
                                  interpolation='none', cmap='gray')
                        ax.set_xticks([])
                        ax.set_yticks([])
                        ri += 1
                    continue
                for c in range(shape[1]):
                    ax = axes[r, c]
                    start = ((r // 2) * shape[1] + c) * step
                    if r % 2 == 0:
                        img = np.transpose(hidden_state[b, start:start+step, :])
                        ax.imshow(img, vmin=hmin, vmax=hmax, interpolation='none', cmap='bwr')
                    else:
                        img = np.transpose(hidden_state_grad[b, start:start+step, :])
                        ax.imshow(img, vmin=0, vmax=gmax, interpolation='none', cmap='gray')
                    ax.set_xticks([])
                    ax.set_yticks([])
            try:
                plt.show()
            except KeyboardInterrupt:
                print('plot show interrupted')
                pass
            cont = input('press "c" to continue')
            if cont != 'c':
                break