#!/usr/bin/python
#
# Captures pictures from a Thorlabs uc480 (software) series cameras.
#
# Hazen 03/12
#

import ctypes
import ctypes.util
import ctypes.wintypes
import numpy
from PIL import Image
import scipy
import scipy.optimize
import time

Handle = ctypes.wintypes.HANDLE

# some definitions
IS_AOI_IMAGE_GET_AOI = 0x0002
IS_AOI_IMAGE_SET_AOI = 0x0001
IS_DONT_WAIT = 0
IS_GET_STATUS = 0x8000
IS_IGNORE_PARAMETER = -1
IS_SEQUENCE_CT = 2
IS_SET_CM_Y8 = 6
IS_SET_GAINBOOST_OFF = 0x0000
IS_SUCCESS = 0
IS_WAIT = 1

class CameraInfo(ctypes.Structure):
    _fields_ = [("SensorID", ctypes.wintypes.WORD),
                ("strSensorName", ctypes.c_char * 32),
                ("nColorMode", ctypes.c_char),
                ("nMaxWidth", ctypes.wintypes.DWORD),
                ("nMaxHeight", ctypes.wintypes.DWORD),
                ("bMasterGain", ctypes.wintypes.BOOL),
                ("bRGain", ctypes.wintypes.BOOL),
                ("bGGain", ctypes.wintypes.BOOL),
                ("bBGain", ctypes.wintypes.BOOL),
                ("bGlobShutter", ctypes.wintypes.BOOL),
                ("Reserved", ctypes.c_char * 16)]

class AOIRect(ctypes.Structure):
    _fields_ = [("s32X", ctypes.wintypes.INT),
                ("s32Y", ctypes.wintypes.INT),
                ("s32Width", ctypes.wintypes.INT),
                ("s32Height", ctypes.wintypes.INT)]

# load the DLL
#uc480_dll = ctypes.util.find_library('uc480')
#if uc480_dll is None:
#    print 'uc480.dll not found'

uc480 = ctypes.cdll.LoadLibrary("c:\windows\system32\uc480_64.dll")

# Helper functions
def check(fn_return):
    if not (fn_return == IS_SUCCESS):
        print "uc480: Call failed with error", fn_return


# Least squares gaussian fitting functions
def fitAFunctionLS(data, params, fn):
    result = params
    errorfunction = lambda p: numpy.ravel(fn(*p)(*numpy.indices(data.shape)) - data)
    good = True
    [result, cov_x, infodict, mesg, success] = scipy.optimize.leastsq(errorfunction, params, full_output = 1, maxfev = 500)
    if (success < 1) or (success > 4):
        print "Fitting problem:", mesg
        good = False
    return [result, good]

def symmetricGaussian(background, height, center_x, center_y, width):
    return lambda x,y: background + height*numpy.exp(-(((center_x-x)/width)**2 + ((center_y-y)/width)**2) * 2)

def fixedEllipticalGaussian(background, height, center_x, center_y, width_x, width_y):
    return lambda x,y: background + height*numpy.exp(-(((center_x-x)/width_x)**2 + ((center_y-y)/width_y)**2) * 2)

def fitSymmetricGaussian(data, sigma):
    params = [numpy.min(data),
              numpy.max(data),
              0.5 * data.shape[0],
              0.5 * data.shape[1],
              2.0 * sigma]
    return fitAFunctionLS(data, params, symmetricGaussian)

def fitFixedEllipticalGaussian(data, sigma):
    params = [numpy.min(data),
              numpy.max(data),
              0.5 * data.shape[0],
              0.5 * data.shape[1],
              2.0 * sigma,
              2.0 * sigma]
    return fitAFunctionLS(data, params, fixedEllipticalGaussian)


# Camera Interface Class
class Camera(Handle):
    def __init__(self, camera_id = 0):
        Handle.__init__(self, 0)

        # Initialize camera.
        check(uc480.is_InitCamera(ctypes.byref(self), ctypes.wintypes.HWND(0)))

        # Get some information about the camera.
        self.info = CameraInfo()
        check(uc480.is_GetSensorInfo(self, ctypes.byref(self.info)))
        self.im_width = self.info.nMaxWidth
        self.im_height = self.info.nMaxHeight

        # Initialize some general camera settings.
        check(uc480.is_SetColorMode(self, IS_SET_CM_Y8))
        check(uc480.is_SetGainBoost(self, IS_SET_GAINBOOST_OFF))
        check(uc480.is_SetGamma(self, 1))
        check(uc480.is_SetHardwareGain(self,
                                       0,
                                       IS_IGNORE_PARAMETER,
                                       IS_IGNORE_PARAMETER,
                                       IS_IGNORE_PARAMETER))

        # Setup capture parameters.
        self.bitpixel = 8     # This is correct for a BW camera anyway..
        self.cur_frame = 0
        self.data = False
        self.id = 0
        self.image = False
        self.running = False
        self.setBuffers()

    def captureImage(self):
        check(uc480.is_FreezeVideo(self, IS_WAIT))
        return self.getImage()

    def getCameraStatus(self, status_code):
        return uc480.is_CameraStatus(self, status_code, IS_GET_STATUS)

    def getImage(self):
        check(uc480.is_CopyImageMem(self, self.image, self.id, self.data.ctypes.data))
        return self.data

    def getNextImage(self):
        while (self.cur_frame == self.getCameraStatus(IS_SEQUENCE_CT)):
            time.sleep(0.05)
        self.cur_frame += 1
        return self.getImage()

    def getSensorInfo(self):
        return self.info

    def setAOI(self, x_start, y_start, width, height):
        self.im_width = width
        self.im_height = height
        aoi_rect = AOIRect(x_start, y_start, width, height)
        check(uc480.is_AOI(self,
                           IS_AOI_IMAGE_SET_AOI,
                           ctypes.byref(aoi_rect),
                           ctypes.sizeof(aoi_rect)))
        self.setBuffers()

    def setBuffers(self):
        self.data = numpy.zeros((self.im_height, self.im_width), dtype = numpy.uint8)
        if self.image:
            check(uc480.is_FreeImageMem(self, self.image, self.id))
        self.image = ctypes.c_char_p()
        self.id = ctypes.c_int()
        check(uc480.is_AllocImageMem(self,
                                     ctypes.c_int(self.im_width),
                                     ctypes.c_int(self.im_height),
                                     ctypes.c_int(self.bitpixel),
                                     ctypes.byref(self.image),
                                     ctypes.byref(self.id)))
        check(uc480.is_SetImageMem(self, self.image, self.id))

    def setFrameRate(self, frame_rate = 1000, verbose = False):
        new_fps = ctypes.c_double()
        check(uc480.is_SetFrameRate(self,
                                    ctypes.c_double(frame_rate),
                                    ctypes.byref(new_fps)))
        if verbose:
            print "uc480: Set frame rate to", new_fps.value, "FPS"

    def shutDown(self):
        check(uc480.is_ExitCamera(self))

    def startCapture(self):
        check(uc480.is_CaptureVideo(self, IS_DONT_WAIT))

    def stopCapture(self):
        check(uc480.is_StopLiveVideo(self, IS_WAIT))

# QPD emulation class
class cameraQPD():
    def __init__(self):
        self.image = None

        # open camera
        self.cam = Camera()

        # set camera AOI
        self.x_start = 652
        self.y_start = 214
        self.x_width = 200
        self.y_width = 200
        self.cam.setAOI(self.x_start,
                        self.y_start,
                        self.x_width,
                        self.y_width)

        # set camera to run as fast as possible
        self.cam.setFrameRate()

        # some derived parameters
        self.half_x = self.x_width/2
        self.half_y = self.y_width/2
        self.X = numpy.arange(self.y_width) - 0.5*float(self.y_width)

        # other variables
        self.fit_size = 10
        self.x_off1 = self.half_x
        self.y_off1 = self.half_y
        self.x_off2 = self.half_x
        self.y_off2 = self.half_y

    def capture(self):
        self.image = self.cam.captureImage()
        return self.image

    def fitGaussian(self, data):
        max_i = data.argmax()
        max_x = int(max_i/data.shape[0])
        max_y = int(max_i%data.shape[0])
        if (max_x > (self.fit_size-1)) and (max_x < (self.x_width - self.fit_size)) and (max_y > (self.fit_size-1)) and (max_y < (self.y_width - self.fit_size)):
            #[params, status] = fitSymmetricGaussian(data[max_x-self.fit_size:max_x+self.fit_size,max_y-self.fit_size:max_y+self.fit_size], 3.0)
            [params, status] = fitFixedEllipticalGaussian(data[max_x-self.fit_size:max_x+self.fit_size,max_y-self.fit_size:max_y+self.fit_size], 6.0)
            params[2] -= self.fit_size/2
            params[3] -= self.fit_size/2
            return [max_x, max_y, params, status]
        else:
            return [False, False, False, False]

    def getImage(self):
        return [self.image, self.x_off1, self.y_off1, self.x_off2, self.y_off2]

    def qpdScan(self, reps = 4):
        power_total = 0.0
        offset_total = 0.0
        good_total = 0.0
        for i in range(reps):
            data = self.singleQpdScan()
            if (data[0] > 0):
                power_total += data[0]
                offset_total += data[1]
                good_total += 1.0
        if (good_total > 0):
            inv_good = 1.0/good_total
            return [power_total * inv_good, offset_total * inv_good, 0]
        else:
            return [0, 0, 0]

    def shutDown(self):
        self.cam.shutDown()

    def singleQpdScan(self):
        data = self.capture().copy()

        # determine offset by moments calculation.
        if 0:
            data_ave = numpy.average(data, axis = 1)
            power = numpy.sum(data_ave)
            x_offset = numpy.sum(self.X * data_ave)
            y_offset = 0.0

        # determine offset by fitting gaussians to the two beam spots.
        if 1:
            power = numpy.max(data)

            if (power < 25):
                return [0, 0, 0]

            # fit first gaussian & subtract
            [max_x, max_y, params, status] = self.fitGaussian(data)
            if (not status):
                return [0, 0, 0]
            data[max_x-self.fit_size:max_x+self.fit_size,max_y-self.fit_size:max_y+self.fit_size] = 0
            self.x_off1 = float(max_x) + params[2] - self.half_x
            self.y_off1 = float(max_y) + params[3] - self.half_y

            # fit second gaussian
            [max_x, max_y, params, status] = self.fitGaussian(data)
            if (not status):
                return [0, 0, 0]
            self.x_off2 = float(max_x) + params[2] - self.half_x
            self.y_off2 = float(max_y) + params[3] - self.half_y

            offset = (abs(self.y_off1 -  self.y_off2)-100.0)*power

        return [power, offset, 0]


# Testing
if __name__ == "__main__":
    cam = Camera()
    reps = 50

    if 0:
        cam.setAOI(772, 566, 200, 200)
        cam.setFrameRate(verbose = True)
        image = cam.captureImage()
        im = Image.fromarray(image)
        im.save("temp.png")

    if 0:
        cam.startCapture()
        st = time.time()
        for i in range(reps):
            print i
            image = cam.getNextImage()
        print "time:", time.time() - st
        cam.stopCapture()

    if 0:
        cam.setAOI(100, 100, 200, 200)
        st = time.time()
        for i in range(reps):
            print i
            image = cam.captureImage()
        print "time:", time.time() - st

    if 1:
        for i in range(50):
            print i
            image = cam.captureImage()
            im = Image.fromarray(image)
            im.save("temp_" + str(i) + ".png")

    cam.shutDown()

    #print image.shape, numpy.min(image), numpy.max(image)

    #im = Image.fromarray(image)
    #im.save("temp.png")
    #im.show()


# Bonus code section..

#    def setSize(self, width, height):
#        if (width > self.info.nMaxWidth) or (height > self.info.nMaxHeight):
#            print "uc480: Width of Height are too large"
#        else:
#            self.im_width = width
#            self.im_height = height
#            self.setBuffers()
        
#    def waitForImage(self, timeout = 100):
#        check(uc480.is_WaitForNextImage(self,
#                                        ctypes.wintypes.UINT(timeout),
#                                        ctypes.byref(self.image),
#                                        ctypes.byref(self.id)))


#
# The MIT License
#
# Copyright (c) 2012 Zhuang Lab, Harvard University
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#
