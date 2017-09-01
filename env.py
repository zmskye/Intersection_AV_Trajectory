import numpy as np
import pyglet
from matplotlib import cm
import matplotlib.pyplot as plt


pyglet.clock.set_fps_limit(1000)


def convert2pixel(meter):
    scale = 7
    pixel = meter * scale
    return pixel


class Env(object):
    action_bound = [-5, 2]
    action_dim = 1
    state_dim = 6
    dt = 0.1    # driving refresh rate
    light_duration = {'red': 25., 'green': 25., 'yellow': 4.}     # seconds
    car_l = 5.      # m
    max_v = 110. / 3.6    # m/s
    car_num_limit = 200
    viewer = None

    def __init__(self, max_p=1500., ave_h=None, fix_start=False, random_light_dur=False, safe_t_gap=None):
        # position(m), velocity(m/s), passed_light, reward
        self.car_info = np.zeros(
            self.car_num_limit,
            np.dtype(dict(names=['id', 'p', 'v', 'pass_l', 'r'], formats=[np.int32, np.float32, np.float32, np.bool, np.float32])))
        self.car_gen_default = dict(p=0., v=0.9 * self.max_v, pass_l=False, r=0)
        self.t_gen = 0.  # counting time for generate car
        self.t_light = 0.  # counting time for light change
        self.is_red_light = True  # red phase
        self.ncs = 0         # n car show
        self.default_headway = ave_h  # used for the interval of generate car
        self.fix_start = fix_start
        self.max_p = max_p    # meter
        self.random_l_dur = random_light_dur
        self.safe_t_gap = 0.4 if safe_t_gap is None else safe_t_gap  # s, (gap/v)   (20/(20+20+3))=0.46

    def step(self, action):
        v = self.car_info['v'][:self.ncs]
        v_ = np.maximum(v + action * self.dt, 0.)  # action (n_car_show, ) filter out negative speed
        self.car_info['p'][:self.ncs] += (v + v_) / 2 * self.dt  # new position
        v[:] = v_
        self.car_info['pass_l'][0] = self.car_info['p'][0] > self.light_p   # new state of passed_light

        self._check_change_light()  # traffic light changes
        s, dx, d2l = self._get_state_norm(return_dx_d2l=True)  # [t2r_norm, t2g_norm, dist2light_norm, dx_norm, dv, v_norm]
        r, done = self._get_r_and_done(dx, d2l)

        # assign r value in self.car_info
        self.car_info['r'][:self.ncs] = r

        # add or delete cars
        new_s = self._gen_or_rm_car(s)  # check to generate car
        return s, r, done, new_s

    def reset(self):
        self.t_gen = 0.  # counting time for generate car
        self.ncs = 0     # n car show
        if self.default_headway is None:
            self.ave_headway = np.random.uniform(1., 4.)        # random headway range
        else:
            self.ave_headway = self.default_headway
        for key in self.car_info.dtype.names:
            v = 0. if key != 'pass_l' else False
            self.car_info[key].fill(v)

        if self.fix_start:
            self.t_light = 0.
            self.is_red_light = True
            c = 'red'
        else:
            if self.random_l_dur:
                # random initial light
                self.light_duration['red'] = self.light_duration['green'] = np.random.uniform(20, 40)
            c = np.random.choice(['red', 'green'])
            self.t_light = np.random.uniform(0, self.light_duration[c])  # counting time for light change
            self.is_red_light = True if c == 'red' else False   # red phase
        if self.viewer is not None:
            self.viewer.light.colors = self.viewer.color[c] * 4
        s = self._gen_or_rm_car(orig_s=None)
        return s

    def _gen_or_rm_car(self, orig_s=None):
        car_changed = False
        # generate car
        if self.t_gen >= self.ave_headway or self.ncs == 0:
            if self.ncs != 0: self.t_gen -= self.ave_headway
            for key in self.car_gen_default:
                self.car_info[key][self.ncs] = self.car_gen_default[key]
            self.car_info['id'][self.ncs] = 0 if self.ncs == 0 else self.car_info['id'][self.ncs-1] + 1
            self.ncs += 1       # add one car
            car_changed = True
        self.t_gen += self.dt  # accumulate time

        # remove car
        if self.car_info['p'][0] >= self.max_p:
            self.car_info = np.roll(self.car_info, -1, axis=0)      # move the 1st row to last, keep its id in memory
            self.ncs -= 1    # remove one car on the road
            car_changed = True

        if car_changed or (orig_s is None):
            new_s = self._get_state_norm(return_dx_d2l=False)
        else:
            new_s = orig_s
        return new_s

    def render(self):
        if self.viewer is None:
            self.viewer = Viewer(self.car_num_limit, self.max_p, self.light_p)
        else:
            if self.viewer.display_game:
                for i, car in enumerate(self.viewer.cars):
                    if i < self.ncs:
                        car.visible = True
                        car.update(self.car_info['p'][i], self.car_info['v'][i], self.car_info['r'][i])
                    else:
                        car.visible = False
                        car.label.text = ''
            self.viewer.render()

    def sample_action(self):    # action (n_car_show, )
        return np.random.uniform(*self.action_bound, self.ncs).astype(np.float32)

    def _check_change_light(self):
        self.t_light += self.dt
        if self.is_red_light:   # red
            if self.t_light >= self.light_duration['red'] + self.light_duration['yellow']:  # turn green
                self.t_light -= (self.light_duration['red'] + self.light_duration['yellow'])
                self.is_red_light = False
                if self.viewer is not None:
                    self.viewer.light.colors = self.viewer.color['green'] * 4
            elif self.t_light >= self.light_duration['yellow']:
                if self.viewer is not None:
                    self.viewer.light.colors = self.viewer.color['red'] * 4
        else:   # green
            if self.t_light >= self.light_duration['green']:    # turn red
                self.t_light -= self.light_duration['green']
                self.is_red_light = True
                if self.viewer is not None:
                    self.viewer.light.colors = self.viewer.color['yellow'] * 4

    def _get_state_norm(self, return_dx_d2l=False):
        """
        0. light_time      (cosine)
        1. light_time derivative   (cosine') (normalized)
        2. distance2light              (normalized)
        3. dx     (leader - follower) (exclude car length, normalized)
        4. dv     (follower - leader) (m/s)   (normalized)
        5. v      (m/s)   (normalized)
        """
        dl = self.light_p - self.car_info['p'][:self.ncs]   # distance to light

        state_mat = np.empty((self.ncs, 6), dtype=np.float32, order='F')

        # t2rg_norm
        if self.is_red_light:
            map_factor = np.pi / (self.light_duration['red'] + self.light_duration['yellow'])
            light_mapping = self.t_light * map_factor
        else:
            map_factor = np.pi / self.light_duration['green']
            light_mapping = self.t_light * map_factor + np.pi
        # light time
        np.cos(light_mapping, out=state_mat[:, 0])
        # norm light time derivative
        state_mat[:, 1] = map_factor * -np.sin(light_mapping) * 10.
        # filter passed
        pass_light_indices = dl < 0
        state_mat[:, 0] = np.where(pass_light_indices, [-1.], state_mat[:, 0])     # pass light is given green light "-1"
        state_mat[:, 1] = np.where(pass_light_indices, [0.], state_mat[:, 1])

        # distance2light_norm (/1000)
        d2l = np.minimum(dl, 2000.)
        state_mat[:, 2] = np.where(pass_light_indices, [2.], d2l / 1000)  # pass light is given large distance to light

        # dx_norm (/100m)
        dx_max = 100.    # meters if no preceding car
        dx = np.concatenate(([100.], -np.diff(self.car_info['p'][:self.ncs])-self.car_l))
        np.minimum(dx, 100., out=dx)     # exclude car length
        np.divide(dx, dx_max, out=state_mat[:, 3])

        # dv_norm (/10)
        state_mat[0, 4] = 0.
        state_mat[1:, 4] = np.diff(self.car_info['v'][:self.ncs])
        np.divide(state_mat[1:, 4],  10., out=state_mat[1:, 4])     # normalize

        # v_norm (/self.max_v)
        np.divide(self.car_info['v'][:self.ncs], self.max_v, out=state_mat[:, 5])

        if return_dx_d2l:
            return state_mat, dx, d2l
        else:
            return state_mat

    def _reward_08_17(self, dx, d2l):
        r = np.empty_like(dx, dtype=np.float32)
        # speed reward
        v_r_max = 1.
        a = v_r_max / self.max_v
        v = self.car_info['v'][:self.ncs]
        more_than_desired = v > self.max_v
        r[:] = v * a

        # check run red
        time2light = d2l / (v + 1e-4)
        not_pass_light_last_step = ~self.car_info['pass_l'][:self.ncs]
        if self.is_red_light:
            t2g = (self.light_duration['yellow'] + self.light_duration['red']) - self.t_light
            run_red = (time2light < t2g) & not_pass_light_last_step
        else:
            t2r = self.light_duration['green'] - self.t_light
            t2g = t2r + (self.light_duration['red'] + self.light_duration['yellow'])
            run_red = (time2light > t2r) & (time2light < t2g) & not_pass_light_last_step
        r[run_red] = -1.

        # time gap reward
        time_gap = dx / (v + 1e-4)
        too_close = time_gap <= self.safe_t_gap
        r[too_close] = -v_r_max

        r[more_than_desired] = -1.
        return r

    def _get_r_and_done(self, dx, d2l):
        done = False
        r = self._reward_08_17(dx, d2l)

        # check collision and too close distance
        if np.any(dx <= 0):
            done = True
        return r, done

    def plot_reward_func(self):
        plt.figure(1)
        v_r_max = 1.
        a = v_r_max / self.max_v
        v = np.linspace(0, 36, 100)
        less_than_desired = v <= self.max_v
        r1 = np.where(less_than_desired, v * a, [-1.])

        r2 = np.empty((20, ))
        h = np.linspace(0, self.safe_t_gap, 20)
        r2[:] = -1

        plt.subplot(121)
        plt.plot(v*3.6, r1)
        plt.subplot(122)
        plt.plot(h, r2)
        plt.show()

    def plot_light_feature(self, light_duration):
        self.light_duration = light_duration
        s = self.reset()
        light, deri_light = [s[0,0]], [s[0,1]]
        for t in range(sum(self.light_duration.values())*10):
            s_, r, done, s = self.step(np.zeros((self.ncs,)))
            light.append(s[self.ncs-1,0])
            deri_light.append(s[self.ncs-1, 1])

        ts = np.arange(len(light))/10
        for i in [1,2]:
            plt.subplot(2, 1, i)
            tp = 0
            tp_ = self.light_duration['yellow']
            plt.plot([tp, tp_], [0, 0], c='y', lw=5, solid_capstyle="butt")
            tp = tp_
            tp_ += self.light_duration['red']
            plt.plot([tp, tp_], [0, 0], c='r', lw=5, solid_capstyle="butt")
            tp = tp_
            tp_ += self.light_duration['green']
            plt.plot([tp, tp_], [0, 0], c='g', lw=5, solid_capstyle="butt")
        ax1 = plt.subplot(2, 1, 1)
        plt.plot(ts, light, 'k')
        plt.setp(ax1.get_xticklabels(), visible=False)
        plt.ylabel('cos')

        plt.subplot(212)
        plt.plot(ts, deri_light, 'k')
        plt.xlabel('Time ($s$)')
        plt.ylabel('deri_cos_norm')
        plt.show()

    def set_fps(self, fps=10):
        pyglet.clock.set_fps_limit(fps)

    @property
    def light_p(self):
        return self.max_p * .95


class Viewer(pyglet.window.Window):
    color = {
        'background': [100/255]*3 + [1],
        'red': [249, 67, 42],
        'green': [74, 214, 49],
        'yellow': [255,255,0],
    }
    fps_display = pyglet.clock.ClockDisplay()
    car_img = pyglet.image.load('car.png')
    display_game = False

    def __init__(self, car_num_limit, max_p, light_p, width=600, height=600,):
        super(Viewer, self).__init__(width, height, resizable=False, caption='RL_car', vsync=False)  # vsync=False to not use the monitor FPS
        self.set_location(x=80, y=10)
        self.set_icon(pyglet.image.load('car.png'))
        pyglet.gl.glClearColor(*self.color['background'])

        min_length = min([width, height])
        center_coord = np.array([min_length, min_length])/2
        self.batch = pyglet.graphics.Batch()
        self.cars = [Car(self.car_img, self.batch, center_coord, max_p) for _ in range(car_num_limit)]

        cr = center_coord[0] * 0.9
        mod_p = convert2pixel(light_p) % (np.pi * np.square(cr))
        radian = mod_p / cr
        shrink_cr = cr * light_p / max_p  # shrink the central radius, dx, dy are shorter
        dx = np.sin(radian) * shrink_cr
        dy = np.cos(radian) * shrink_cr
        light_coord = center_coord[0] + dx, center_coord[1] - dy
        light_box = [light_coord[0] - 10, light_coord[1] - 10,
                     light_coord[0] + 10, light_coord[1] - 10,
                     light_coord[0] + 10, light_coord[1] + 10,
                     light_coord[0] - 10, light_coord[1] + 10]
        self.light = self.batch.add(4, pyglet.gl.GL_QUADS, None, ('v2f', light_box), ('c3B', self.color['red']*4))

    def render(self):
        self.dispatch_events()
        if self.display_game:
            pyglet.clock.tick()
            self.switch_to()
            self.dispatch_event('on_draw')
            self.flip()

    def on_draw(self):
        self.clear()
        self.batch.draw()
        self.fps_display.draw()

    def on_key_press(self, symbol, modifiers):
        if modifiers & pyglet.window.key.MOD_CTRL and symbol == pyglet.window.key.L:
            pass
        elif symbol == pyglet.window.key.UP:
            pyglet.clock.set_fps_limit(1000)
        elif symbol == pyglet.window.key.DOWN:
            pyglet.clock.set_fps_limit(10)
        elif symbol == pyglet.window.key.SPACE:
            if self.display_game is False:
                self.display_game = True
            else:
                self.display_game = False


class Car(pyglet.sprite.Sprite):
    l = 4  # length of car

    def __init__(self, img, batch, geo_center, max_p):
        super(Car, self).__init__(img=img, batch=batch, usage='stream', )

        # new car attributes
        self.max_p = max_p
        self.gc, self.cr = geo_center, geo_center[0] * 0.9  # geo center and center radius, unit=pixel
        self.g_circumference = np.pi * np.square(self.cr)   # unit=pixel

        # text describe
        self.label = pyglet.text.Label(
            text='', font_size=7, bold=True,
            anchor_x='center', anchor_y='center', align='center', batch=batch)

        # supered attributes
        self.visible = False
        self.scale = convert2pixel(self.l) / self.image.width    # width is the car length
        self.image.anchor_x = self.image.width / 2
        self.image.anchor_y = self.image.height / 2

    def update(self, p, v, r):
        p_percent = p / self.max_p  # how many road left
        mod_p = convert2pixel(p) % self.g_circumference
        radian = mod_p / self.cr
        shrink_cr = self.cr * p_percent    # shrink the central radius, dx, dy are shorter
        dx = np.sin(radian) * shrink_cr
        dy = np.cos(radian) * shrink_cr

        # these are supered attributes
        self.scale = (p_percent+.1) * convert2pixel(self.l) / self.image.width  # shrink the image size
        self.x, self.y, self.rotation = self.gc[0] + dx, self.gc[1] - dy, -np.rad2deg(radian)

        # label
        r_color = cm.rainbow(int((-r+1)/2*255))  # the value should in a range of (0, 255), the RGB max=1
        self.label.text = '%.0f/%.2f' % (v*3.6, r)
        self.label.x, self.label.y = int(self.gc[0] + dx*1.1), int(self.gc[1] - dy*1.1)
        self.label.color = (np.array(r_color)*255).astype(np.int32).tolist()    # has to be a tuple or list with max=255


if __name__ == '__main__':
    np.random.seed(1)
    env = Env(fix_start=True)
    env.plot_reward_func()
    env.plot_light_feature(light_duration={'yellow': 3, 'red': 40, 'green': 40})
    # env.set_fps(60)
    for i in range(2):
        s = env.reset()
        for _ in range(500):
            env.render()
            a = np.zeros((env.ncs, ))
            s_, r, done, s = env.step(a)
            # (t2rg, distance2light, dx, dv, v_norm)
            if done:
                break

