import numpy as np
import matplotlib.pyplot as plt
import sys

# mian_tab
#    50     60      80   100  120   150   
#24  12     2       3     6    0    4
#30   3     0       3     3    0    0
#50   0     5       5     0    5    0
#60   6     0       3     3    0    2
#120  0     5       5     0    0    4


#   camera params
VIDEO_FRAME_RATE    =   30      #fps
EXPOSURE_TIME       =   1e-3    #s
SIMUE_TIME          =   1       #s

#   light params
DC_AMP              =   5       #DC_strength
DC_FREQ             =   0.25       #DC_FREQ
DC_INITIAL_PHASE    =   0       #INITIAL_PHASE WITH CAMERA
FLICKER_AMP         =   1       #AC_strenght
FLICKER_FREQ        =   50      #AC_FREQ
AC_INITIAL_PHASE    =   1       #INITIAL_PHASE WITH CAMERA

SAMPLE_FREQ         =   VIDEO_FRAME_RATE * 1E5     #SAMPLE_FREQ


#check for max exposure time
if VIDEO_FRAME_RATE :
    max_exposure_time = 1.0 / VIDEO_FRAME_RATE 
else :
    print("帧率设置错误，重新设置")
    sys.exit()
    
if EXPOSURE_TIME > max_exposure_time :
    print (f"帧率限制最大曝光时间({max_exposure_time: .4f}ms),曝光时间({EXPOSURE_TIME: .4f})超出帧率限制,脚本结束")
    sys.exit()

# set the DC_AC light source
dt              = 1.0 / SAMPLE_FREQ
time_global     = np.arange(0,SIMUE_TIME, dt)
I_DC            = DC_AMP * np.abs(np.sin(2*np.pi*DC_FREQ*time_global+DC_INITIAL_PHASE))
I_AC            = FLICKER_AMP * np.abs(np.sin(2*np.pi*FLICKER_FREQ*time_global+AC_INITIAL_PHASE))
I_TOTAL         = I_DC + I_AC

#set each frame exposure time
num_frames                  = int(np.floor(SIMUE_TIME * VIDEO_FRAME_RATE))
frame_exposure_time_start   = np.arange(num_frames)*max_exposure_time
frame_strength              = np.zeros(num_frames)
max                         = 0.0
min                         = 1E6   
max_min_ratio               = 0.0

# caculate strength curve
for i, frame_start in enumerate(frame_exposure_time_start):
    frame_exposure_start    = frame_start
    frame_exposure_end      = frame_start + EXPOSURE_TIME
    idx_ITOTAL_start        = int(np.round(frame_exposure_start/dt))
    idx_ITOTAL_end          = int(np.round(frame_exposure_end/dt))
    if idx_ITOTAL_end > len(I_TOTAL):
        idx_ITOTAL_end      = len(I_TOTAL)
    if idx_ITOTAL_start < idx_ITOTAL_end:
        frame_strength[i]   = round(np.sum(I_TOTAL[idx_ITOTAL_start:idx_ITOTAL_end]),3)
        if frame_strength[i] > max:
            max = frame_strength[i]
        if frame_strength[i] < min:
            min = frame_strength[i]
        print(f"当前帧索引({i}),当前帧亮度({frame_strength[i]}),max({max}),min({min})")
    else:
        frame_strength[i]   = 0
        print(f"错误帧,设置帧亮度:0") 
max_min_ratio = max / min
print(f"最大亮度值({max}),最小亮度值({min}),最大最小亮度比值({max_min_ratio: .2f})")
# visual for data

fig = plt.figure(1)
ax1 = fig.add_subplot(3,2,1)
ax1.plot(time_global, I_DC)
ax1.set_title('Light DC')
ax2 = fig.add_subplot(3,2,2)
ax2.plot(time_global, I_AC)
ax2.set_title('Light AC')
ax3 = fig.add_subplot(3,1,2)
ax3.plot(time_global, I_TOTAL)
ax3.set_title('Light Source')
ax4 = fig.add_subplot(3,1,3)
ax4.plot(frame_strength,marker='o', linestyle='-')
ax4.set_title(f'Frame Strength Curve ratio={max_min_ratio: .2f}')

plt.show()