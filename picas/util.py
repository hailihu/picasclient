# SIM-CITY client
#
# Copyright 2015 Joris Borgdorff <j.borgdorff@esciencecenter.nl>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import os
import time
import glob
import shutil
from copy import deepcopy


def merge_dicts(dict1, dict2):
    merge = deepcopy(dict1)
    merge.update(dict2)
    return merge


def seconds():
    return int(time.time())


class Timer(object):

    def __init__(self):
        self.t = time.time()

    def elapsed(self):
        return time.time() - self.t

    def reset(self):
        new_t = time.time()
        diff = new_t - self.t
        self.t = new_t
        return diff
