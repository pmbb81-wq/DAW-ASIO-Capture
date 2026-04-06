#pragma once
// Minimal ASIO type definitions — no Steinberg SDK required.
// Matches the ABI of ASIO 2.3.

#include <unknwn.h>   // IUnknown, REFIID, HRESULT

typedef double          ASIOSampleRate;
typedef long long       ASIOSamples;
typedef long long       ASIOTimeStamp;
typedef long            ASIOBool;
typedef long            ASIOError;

#define ASIOTrue  1
#define ASIOFalse 0

#define ASE_OK              0
#define ASE_SUCCESS         0x3f4847a0L
#define ASE_NotPresent      (-1000)
#define ASE_HWMalfunction   (-999)
#define ASE_InvalidParameter (-998)
#define ASE_InvalidMode     (-997)
#define ASE_SPNotAdvancing  (-996)
#define ASE_NoClock         (-995)
#define ASE_NoMemory        (-994)

enum ASIOSampleType {
    ASIOSTInt16MSB   = 0,
    ASIOSTInt24MSB   = 1,
    ASIOSTInt32MSB   = 2,
    ASIOSTFloat32MSB = 3,
    ASIOSTFloat64MSB = 4,
    ASIOSTInt32MSB16 = 8,
    ASIOSTInt32MSB18 = 9,
    ASIOSTInt32MSB20 = 10,
    ASIOSTInt32MSB24 = 11,
    ASIOSTInt16LSB   = 16,
    ASIOSTInt24LSB   = 17,
    ASIOSTInt32LSB   = 18,
    ASIOSTFloat32LSB = 19,
    ASIOSTFloat64LSB = 20,
    ASIOSTInt32LSB16 = 24,
    ASIOSTInt32LSB18 = 25,
    ASIOSTInt32LSB20 = 26,
    ASIOSTInt32LSB24 = 27,
    ASIOSTLastEntry
};

struct ASIOClockSource {
    long     index;
    long     associatedChannel;
    long     associatedGroup;
    ASIOBool isCurrentSource;
    char     name[32];
};

struct ASIOChannelInfo {
    long           channel;
    ASIOBool       isInput;
    ASIOBool       isActive;
    long           channelGroup;
    ASIOSampleType type;
    char           name[32];
};

struct ASIOBufferInfo {
    ASIOBool isInput;
    long     channelNum;
    void    *buffers[2];
};

struct ASIOTimeInfo {
    double         speed;
    ASIOTimeStamp  systemTime;
    ASIOSamples    samplePosition;
    ASIOSampleRate sampleRate;
    unsigned long  flags;
    char           reserved[12];
};

struct ASIOTime {
    long         reserved[4];
    ASIOTimeInfo timeInfo;
};

typedef void       (*FnBufferSwitch)(long index, ASIOBool directProcess);
typedef void       (*FnSampleRateDidChange)(ASIOSampleRate sRate);
typedef long       (*FnAsioMessage)(long selector, long value, void *message, double *opt);
typedef ASIOTime * (*FnBufferSwitchTimeInfo)(ASIOTime *params, long index, ASIOBool directProcess);

struct ASIOCallbacks {
    FnBufferSwitch         bufferSwitch;
    FnSampleRateDidChange  sampleRateDidChange;
    FnAsioMessage          asioMessage;
    FnBufferSwitchTimeInfo bufferSwitchTimeInfo;
};

// IASIO COM interface.
// ASIO convention: each driver's CLSID *is* its interface IID — no fixed IID.
// We declare it without __declspec(uuid) for that reason.
struct __declspec(novtable) IASIO : public IUnknown {
    virtual ASIOBool  init(void *sysHandle) = 0;
    virtual void      getDriverName(char *name) = 0;
    virtual long      getDriverVersion() = 0;
    virtual void      getErrorMessage(char *string) = 0;
    virtual ASIOError start() = 0;
    virtual ASIOError stop() = 0;
    virtual ASIOError getChannels(long *numInputChannels, long *numOutputChannels) = 0;
    virtual ASIOError getLatencies(long *inputLatency, long *outputLatency) = 0;
    virtual ASIOError getBufferSize(long *minSize, long *maxSize,
                                    long *preferredSize, long *granularity) = 0;
    virtual ASIOError canSampleRate(ASIOSampleRate sampleRate) = 0;
    virtual ASIOError getSampleRate(ASIOSampleRate *sampleRate) = 0;
    virtual ASIOError setSampleRate(ASIOSampleRate sampleRate) = 0;
    virtual ASIOError getClockSources(ASIOClockSource *clocks, long *numSources) = 0;
    virtual ASIOError setClockSource(long reference) = 0;
    virtual ASIOError getSamplePosition(ASIOSamples *sPos, ASIOTimeStamp *tStamp) = 0;
    virtual ASIOError getChannelInfo(ASIOChannelInfo *info) = 0;
    virtual ASIOError createBuffers(ASIOBufferInfo *bufferInfos, long numChannels,
                                    long bufferSize, ASIOCallbacks *callbacks) = 0;
    virtual ASIOError disposeBuffers() = 0;
    virtual ASIOError controlPanel() = 0;
    virtual ASIOError future(long selector, void *opt) = 0;
    virtual ASIOError outputReady() = 0;
};
